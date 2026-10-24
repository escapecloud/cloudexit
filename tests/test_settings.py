import contextlib
import io
import os
import tempfile
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from cloudexit.cli import app
from tests.support.isolation import isolated_environ
from cloudexit import codes
from cloudexit.settings import (
    DEFAULT_TIMEOUT,
    Settings,
    SettingsError,
    config_file_path,
    describe,
    load_settings,
    mask_key,
    normalize_host,
)

LOGGER = "cloudexit.settings"
KEY = "sk_live_0123456789abcdef"


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = patch("cloudexit.settings.legacy_config", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def config_path(self):
        return self.tmp / "config" / "cloudexit" / "config.toml"

    def write_config(self, text, mode=0o600):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(text, encoding="utf-8")
        self.config_path.chmod(mode)
        return self.config_path

    def write_key(self, name="api-key", key=KEY, mode=0o600):
        path = self.tmp / name
        path.write_text(f"{key}\n", encoding="utf-8")
        path.chmod(mode)
        return str(path)

    def load(self, flags=None, env=None):
        environ = {**isolated_environ(self.tmp), **(env or {})}
        return load_settings(flags, environ=environ)

    def legacy(self, **values):
        return patch(
            "cloudexit.settings.legacy_config", types.SimpleNamespace(**values)
        )


class ConfigFilePathTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_uses_xdg_config_home_when_absolute(self):
        path = config_file_path({"XDG_CONFIG_HOME": "/xdg"}, platform="linux")
        self.assertEqual(path, Path("/xdg/cloudexit/config.toml"))

    def test_defaults_to_dot_config_in_the_home_directory(self):
        expected = Path.home() / ".config" / "cloudexit" / "config.toml"
        self.assertEqual(config_file_path({}, platform="linux"), expected)

    @unittest.skipIf(os.name == "nt", "absolute /xdg paths are POSIX paths")
    def test_ignores_a_relative_xdg_config_home(self):
        expected = Path.home() / ".config" / "cloudexit" / "config.toml"
        path = config_file_path({"XDG_CONFIG_HOME": "relative"}, platform="linux")
        self.assertEqual(path, expected)

    def test_uses_appdata_on_windows(self):
        path = config_file_path({"APPDATA": "/appdata"}, platform="win32")
        self.assertEqual(path, Path("/appdata/cloudexit/config.toml"))


class NormalizeHostTests(unittest.TestCase):
    def test_adds_https_when_there_is_no_scheme(self):
        self.assertEqual(normalize_host("example.com"), "https://example.com")

    def test_keeps_https_and_strips_the_trailing_slash(self):
        self.assertEqual(
            normalize_host(" https://example.com/ "), "https://example.com"
        )

    def test_rejects_plain_http_for_remote_hosts(self):
        with self.assertRaises(SettingsError) as ctx:
            normalize_host("http://example.com")
        self.assertIn("https://", str(ctx.exception))

    def test_accepts_plain_http_for_localhost(self):
        self.assertEqual(
            normalize_host("http://localhost:8000"), "http://localhost:8000"
        )
        self.assertEqual(normalize_host("http://127.0.0.1"), "http://127.0.0.1")

    def test_rejects_other_schemes_and_empty_values(self):
        for value in ("ftp://example.com", "", "  ", "https://"):
            with self.assertRaises(SettingsError, msg=value):
                normalize_host(value)


class PrecedenceTests(SettingsTestCase):
    def test_nothing_configured_is_all_unset(self):
        settings = self.load()

        self.assertIsNone(settings.host.value)
        self.assertIsNone(settings.api_key.value)
        self.assertEqual(settings.timeout.value, DEFAULT_TIMEOUT)
        self.assertEqual(settings.timeout.source, "default")
        self.assertFalse(settings.config_found)

    def test_host_flag_over_env_over_file_over_legacy(self):
        self.write_config('[default]\nhost = "file.test"\n')
        env = {
            "CLOUDEXIT_HOST": "env.test",
            "HOST": "legacy-env.test",
            "KEY": "legacy-key",
        }
        with self.legacy(HOST="legacy-config.test", KEY="legacy-key"):
            steps = [
                (
                    self.load({"host": "flag.test"}, env),
                    "https://flag.test",
                    "flag --host",
                ),
                (self.load(None, env), "https://env.test", "env CLOUDEXIT_HOST"),
                (
                    self.load(None, {"HOST": "legacy-env.test", "KEY": "k"}),
                    "https://file.test",
                    "config file [default]",
                ),
            ]
            self.config_path.unlink()
            steps += [
                (
                    self.load(None, {"HOST": "legacy-env.test", "KEY": "k"}),
                    "https://legacy-env.test",
                    "legacy env HOST",
                ),
                (self.load(), "https://legacy-config.test", "legacy config.py"),
            ]

        for settings, value, source in steps:
            self.assertEqual(settings.host.value, value)
            self.assertEqual(settings.host.source, source)

    def test_each_value_is_resolved_on_its_own(self):
        ca_bundle = self.tmp / "ca.pem"
        ca_bundle.write_text("pem")
        self.write_config(
            f"[default]\nassessment_id = 'from-file'\nca_bundle = '{ca_bundle}'\n"
        )

        settings = self.load(
            {"host": "flag.test"},
            {"CLOUDEXIT_ASSESSMENT_ID": "from-env", "CLOUDEXIT_TIMEOUT": "30"},
        )

        self.assertEqual(settings.host.source, "flag --host")
        self.assertEqual(settings.assessment_id.value, "from-env")
        self.assertEqual(settings.assessment_id.source, "env CLOUDEXIT_ASSESSMENT_ID")
        self.assertEqual(settings.ca_bundle.value, str(ca_bundle))
        self.assertEqual(settings.ca_bundle.source, "config file [default]")
        self.assertEqual(settings.timeout.value, 30.0)
        self.assertEqual(settings.timeout.source, "env CLOUDEXIT_TIMEOUT")

    def test_timeout_flag_overrides_env(self):
        settings = self.load({"timeout": 5.0}, {"CLOUDEXIT_TIMEOUT": "30"})
        self.assertEqual(settings.timeout.value, 5.0)
        self.assertEqual(settings.timeout.source, "flag --timeout")

    def test_invalid_timeouts_are_rejected(self):
        for value in ("soon", "0", "-3"):
            with self.assertRaises(SettingsError, msg=value):
                self.load(None, {"CLOUDEXIT_TIMEOUT": value})
        with self.assertRaises(SettingsError):
            self.load({"timeout": 0.0})

    def test_upload_is_a_mode_from_a_flag_env_or_file(self):
        self.assertEqual(self.load(None).upload.value, "auto")
        self.assertEqual(self.load(None).upload.source, "default")
        self.assertEqual(self.load({"upload": "required"}).upload.value, "required")
        self.assertEqual(
            self.load(None, {"CLOUDEXIT_UPLOAD": "OFF"}).upload.value, "off"
        )
        self.write_config('[default]\nupload = "required"\n')
        settings = self.load()
        self.assertEqual(settings.upload.value, "required")
        self.assertEqual(settings.upload.source, "config file [default]")
        self.assertEqual(
            self.load({"upload": "off"}, {"CLOUDEXIT_UPLOAD": "required"}).upload.value,
            "off",
        )

    def test_the_old_boolean_upload_values_still_map_to_a_mode(self):
        self.assertEqual(
            self.load(None, {"CLOUDEXIT_UPLOAD": "Yes"}).upload.value, "auto"
        )
        self.assertEqual(
            self.load(None, {"CLOUDEXIT_UPLOAD": "false"}).upload.value, "off"
        )
        self.write_config("[default]\nupload = false\n")
        self.assertEqual(self.load().upload.value, "off")

    def test_an_unknown_upload_mode_names_its_source(self):
        with self.assertRaises(SettingsError) as ctx:
            self.load(None, {"CLOUDEXIT_UPLOAD": "maybe"})
        self.assertIn("env CLOUDEXIT_UPLOAD", str(ctx.exception))
        self.assertIn("auto, required or off", str(ctx.exception))

    def test_http_host_is_rejected_with_its_source(self):
        with self.assertRaises(SettingsError) as ctx:
            self.load(None, {"CLOUDEXIT_HOST": "http://example.com"})
        self.assertIn("env CLOUDEXIT_HOST", str(ctx.exception))

    def test_local_http_host_is_accepted(self):
        settings = self.load({"host": "http://localhost:8000"})
        self.assertEqual(settings.host.value, "http://localhost:8000")

    def test_a_host_without_scheme_gets_https(self):
        self.assertEqual(
            self.load({"host": "example.com"}).host.value, "https://example.com"
        )

    def test_a_lower_precedence_bad_host_is_not_validated(self):
        self.write_config('[default]\nhost = "http://example.com"\n')
        self.assertEqual(
            self.load({"host": "flag.test"}).host.value, "https://flag.test"
        )

    def test_spool_dir_and_catalogue_url_come_from_the_environment(self):
        settings = self.load(
            None,
            {
                "CLOUDEXIT_SPOOL_DIR": str(self.tmp / "spool"),
                "CLOUDEXIT_CATALOGUE_URL": "https://data.example.test/db.gz",
            },
        )
        self.assertEqual(settings.spool_dir.value, str(self.tmp / "spool"))
        self.assertEqual(settings.catalogue_url.source, "env CLOUDEXIT_CATALOGUE_URL")

    def test_malformed_config_file_is_a_clear_error(self):
        self.write_config("[default\n")
        with self.assertRaises(SettingsError) as ctx:
            self.load()
        self.assertIn(str(self.config_path), str(ctx.exception))

    def test_wrong_value_types_in_the_file_are_rejected(self):
        self.write_config("[default]\nhost = 5\n")
        with self.assertRaises(SettingsError):
            self.load()

    def test_unknown_keys_in_the_file_warn(self):
        self.write_config('[default]\nhots = "typo.test"\n')
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.load()
        self.assertIn("hots", logs.output[0])

    def test_missing_ca_bundle_is_rejected(self):
        with self.assertRaises(SettingsError):
            self.load({"ca_bundle": str(self.tmp / "missing.pem")})


class ProfileTests(SettingsTestCase):
    CONFIG = """
[default]
host = "default.test"
assessment_id = "default-assessment"

[profiles.staging]
host = "staging.test"
"""

    def test_profile_from_flag_overrides_default_values(self):
        self.write_config(self.CONFIG)
        settings = self.load({"profile": "staging"})

        self.assertEqual(settings.host.value, "https://staging.test")
        self.assertEqual(settings.host.source, "config file [profiles.staging]")
        self.assertEqual(settings.profile.source, "flag --config-profile")

    def test_unset_profile_values_fall_back_to_default(self):
        self.write_config(self.CONFIG)
        settings = self.load(None, {"CLOUDEXIT_PROFILE": "staging"})

        self.assertEqual(settings.assessment_id.value, "default-assessment")
        self.assertEqual(settings.assessment_id.source, "config file [default]")
        self.assertEqual(settings.profile.source, "env CLOUDEXIT_PROFILE")

    def test_without_a_profile_only_default_is_used(self):
        self.write_config(self.CONFIG)
        self.assertEqual(self.load().host.value, "https://default.test")

    def test_unknown_profile_is_an_error(self):
        self.write_config(self.CONFIG)
        with self.assertRaises(SettingsError) as ctx:
            self.load({"profile": "prod"})
        self.assertIn("prod", str(ctx.exception))

    def test_profile_without_a_config_file_is_an_error(self):
        with self.assertRaises(SettingsError):
            self.load({"profile": "staging"})

    def test_flag_still_beats_the_profile(self):
        self.write_config(self.CONFIG)
        settings = self.load({"profile": "staging", "host": "flag.test"})
        self.assertEqual(settings.host.value, "https://flag.test")


class KeyFileTests(SettingsTestCase):
    def test_key_is_read_from_the_flag_file(self):
        settings = self.load({"api_key_file": self.write_key()})

        self.assertEqual(settings.api_key.value, KEY)
        self.assertEqual(settings.api_key.source, "flag --api-key-file")
        self.assertEqual(settings.api_key_file.value, str(self.tmp / "api-key"))

    def test_env_key_file_and_env_key(self):
        from_file = self.load(None, {"CLOUDEXIT_API_KEY_FILE": self.write_key()})
        self.assertEqual(from_file.api_key.value, KEY)
        self.assertEqual(from_file.api_key.source, "env CLOUDEXIT_API_KEY_FILE")

        inline = self.load(None, {"CLOUDEXIT_API_KEY": "inline-key-123456"})
        self.assertEqual(inline.api_key.value, "inline-key-123456")
        self.assertEqual(inline.api_key.source, "env CLOUDEXIT_API_KEY")
        self.assertIsNone(inline.api_key_file.value)

    def test_key_file_wins_over_an_inline_key_in_the_same_layer(self):
        env = {
            "CLOUDEXIT_API_KEY_FILE": self.write_key(),
            "CLOUDEXIT_API_KEY": "inline-key-123456",
        }
        self.assertEqual(self.load(None, env).api_key.value, KEY)

    def test_a_higher_layer_inline_key_beats_a_lower_layer_key_file(self):
        self.write_config(f"[default]\napi_key_file = '{self.write_key()}'\n")
        settings = self.load(None, {"CLOUDEXIT_API_KEY": "inline-key-123456"})
        self.assertEqual(settings.api_key.value, "inline-key-123456")

    def test_relative_key_file_in_the_config_is_relative_to_the_config(self):
        self.write_key("keys-api", key="relative-key-123456")
        self.write_config('[default]\napi_key_file = "../../keys-api"\n')

        settings = self.load()

        self.assertEqual(settings.api_key.value, "relative-key-123456")
        self.assertEqual(settings.api_key.source, "config file [default]")

    def test_key_in_the_config_file(self):
        self.write_config('[default]\napi_key = "file-key-0123456789"\n')
        settings = self.load()
        self.assertEqual(settings.api_key.value, "file-key-0123456789")

    def test_missing_and_empty_key_files_are_errors(self):
        with self.assertRaises(SettingsError):
            self.load({"api_key_file": str(self.tmp / "missing")})
        empty = self.tmp / "empty"
        empty.write_text("\n")
        with self.assertRaises(SettingsError):
            self.load({"api_key_file": str(empty)})

    def test_the_key_never_appears_in_a_repr(self):
        settings = self.load({"api_key_file": self.write_key()})
        self.assertNotIn(KEY, repr(settings))


@unittest.skipUnless(os.name == "posix", "POSIX permissions")
class PermissionWarningTests(SettingsTestCase):
    def test_config_file_readable_by_others_warns(self):
        path = self.write_config('[default]\nhost = "x.test"\n', mode=0o644)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.load()
        self.assertIn(str(path), logs.output[0])
        self.assertIn("chmod 600", logs.output[0])

    def test_private_config_file_does_not_warn(self):
        self.write_config('[default]\nhost = "x.test"\n', mode=0o600)
        with self.assertNoLogs(LOGGER, level="WARNING"):
            self.load()

    def test_key_file_readable_by_group_warns(self):
        path = self.write_key(mode=0o640)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.load({"api_key_file": path})
        self.assertIn(path, logs.output[0])
        self.assertNotIn(KEY, logs.output[0])

    def test_private_key_file_does_not_warn(self):
        with self.assertNoLogs(LOGGER, level="WARNING"):
            self.load({"api_key_file": self.write_key(mode=0o600)})


class LegacySourceTests(SettingsTestCase):
    def test_host_and_key_env_vars_are_used_together_with_a_warning(self):
        env = {"HOST": "legacy.test", "KEY": "legacy-key-0123456"}
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            settings = self.load(None, env)

        self.assertEqual(settings.host.value, "https://legacy.test")
        self.assertEqual(settings.host.source, "legacy env HOST")
        self.assertEqual(settings.api_key.value, "legacy-key-0123456")
        self.assertEqual(settings.api_key.source, "legacy env KEY")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("deprecated", logs.output[0])
        self.assertNotIn("legacy-key-0123456", logs.output[0])

    def test_host_alone_or_key_alone_is_ignored(self):
        for env in ({"HOST": "legacy.test"}, {"KEY": "legacy-key-0123456"}):
            with self.assertNoLogs(LOGGER, level="WARNING"):
                settings = self.load(None, env)
            self.assertIsNone(settings.host.value)
            self.assertIsNone(settings.api_key.value)

    def test_config_py_is_used_with_a_warning(self):
        with (
            self.legacy(HOST="old.test", KEY="old-key-0123456789"),
            self.assertLogs(LOGGER, level="WARNING") as logs,
        ):
            settings = self.load()

        self.assertEqual(settings.host.value, "https://old.test")
        self.assertEqual(settings.host.source, "legacy config.py")
        self.assertEqual(settings.api_key.source, "legacy config.py")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("config.py is deprecated", logs.output[0])
        self.assertNotIn("old-key-0123456789", logs.output[0])

    def test_env_pair_beats_config_py(self):
        env = {"HOST": "legacy.test", "KEY": "legacy-key-0123456"}
        with self.legacy(HOST="old.test", KEY="old-key"):
            with self.assertLogs(LOGGER, level="WARNING"):
                settings = self.load(None, env)
        self.assertEqual(settings.host.source, "legacy env HOST")

    def test_no_warning_when_a_higher_source_covers_everything(self):
        with self.legacy(HOST="old.test", KEY="old-key-0123456789"):
            with self.assertNoLogs(LOGGER, level="WARNING"):
                settings = self.load(
                    {"host": "flag.test"}, {"CLOUDEXIT_API_KEY": "inline-key-123456"}
                )
        self.assertEqual(settings.host.source, "flag --host")

    def test_an_empty_config_py_or_a_namespace_package_is_ignored(self):
        for legacy in (types.SimpleNamespace(), types.SimpleNamespace(HOST="", KEY="")):
            with self.legacy(**vars(legacy)):
                settings = self.load()
            self.assertIsNone(settings.host.value)


class DescribeTests(SettingsTestCase):
    def test_masks_the_key_to_prefix_and_last_four(self):
        for prefix in ("esc_sk_", "exc_eu_sk_", "exc_us_sk_"):
            with self.subTest(prefix=prefix):
                self.assertEqual(mask_key(f"{prefix}AAAABBBBCCCC"), f"{prefix}...CCCC")
        self.assertEqual(mask_key(KEY), "sk_l...cdef")
        self.assertEqual(mask_key("short"), "****")
        self.assertEqual(mask_key("esc_sk_abcd"), "****")
        self.assertEqual(mask_key(None), "-")

    def test_output_lists_values_with_sources_and_never_the_full_key(self):
        settings = self.load(
            {"host": "flag.test", "api_key_file": self.write_key()},
            {"CLOUDEXIT_ASSESSMENT_ID": "a-1"},
        )
        text = "\n".join(describe(settings))

        self.assertNotIn(KEY, text)
        self.assertIn("sk_l...cdef", text)
        self.assertIn("https://flag.test", text)
        self.assertIn("flag --host", text)
        self.assertIn("flag --api-key-file", text)
        self.assertIn("env CLOUDEXIT_ASSESSMENT_ID", text)
        self.assertIn("(not found)", text)

    def test_unset_values_are_shown_as_unset(self):
        lines = describe(Settings())
        host_line = next(line for line in lines if line.startswith("host"))
        self.assertIn("-", host_line)
        self.assertIn("unset", host_line)


class ConfigCommandTests(SettingsTestCase):
    def run_main(self, argv, env=None):
        environ = {**isolated_environ(self.tmp), **(env or {})}
        out = io.StringIO()
        with (
            patch("sys.argv", ["cloudexit", *argv]),
            patch.dict(os.environ, environ, clear=True),
            patch("cloudexit.cli.app.prepare_catalogue") as mock_dataset,
            patch("cloudexit.cli.app.console.print") as mock_print,
            contextlib.redirect_stdout(out),
        ):
            try:
                app.main()
                code = None
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), code, mock_dataset, mock_print

    def test_config_path_prints_the_location_without_banner_or_dataset(self):
        out, code, mock_dataset, mock_print = self.run_main(["config", "path"])

        self.assertIsNone(code)
        self.assertEqual(out.strip(), str(self.config_path))
        mock_dataset.assert_not_called()
        mock_print.assert_not_called()

    def test_config_show_masks_keys(self):
        out, code, mock_dataset, _ = self.run_main(
            ["config", "show", "--host", "flag.test"],
            {"CLOUDEXIT_API_KEY": KEY},
        )

        self.assertIsNone(code)
        self.assertNotIn(KEY, out)
        self.assertIn("sk_l...cdef", out)
        self.assertIn("https://flag.test", out)
        self.assertIn("flag --host", out)
        mock_dataset.assert_not_called()

    def test_config_show_with_a_bad_host_exits_with_the_config_code(self):
        _, code, _, mock_print = self.run_main(
            ["config", "show"], {"CLOUDEXIT_HOST": "http://example.com"}
        )

        self.assertEqual(code, codes.CONFIG)
        self.assertIn("https://", mock_print.call_args.args[0])

    def test_config_requires_a_subcommand(self):
        with (
            patch("sys.argv", ["cloudexit", "config"]),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            app.parse_arguments()


class ArgumentTests(unittest.TestCase):
    def parse(self, *argv):
        with patch("sys.argv", ["cloudexit", *argv]):
            return app.parse_arguments()

    def test_platform_flags_on_aws_and_azure(self):
        for provider in ("aws", "azure"):
            args = self.parse(
                provider,
                "--host",
                "platform.test",
                "--api-key-file",
                "/k",
                "--config-profile",
                "staging",
                "--assessment-id",
                "a-1",
                "--ca-bundle",
                "/ca.pem",
                "--timeout",
                "7.5",
            )
            self.assertEqual(args.host, "platform.test")
            self.assertEqual(args.api_key_file, "/k")
            self.assertEqual(args.config_profile, "staging")
            self.assertEqual(args.assessment_id, "a-1")
            self.assertEqual(args.ca_bundle, "/ca.pem")
            self.assertEqual(args.timeout, 7.5)

    def test_aws_profile_still_means_the_aws_profile(self):
        args = self.parse("aws", "--profile", "my-aws")
        self.assertEqual(args.profile, "my-aws")
        self.assertIsNone(args.config_profile)

    def test_main_resolves_settings_before_the_dataset_download(self):
        args = Namespace(cloud_provider="aws")
        with (
            patch("cloudexit.cli.app.parse_arguments", return_value=args),
            patch(
                "cloudexit.cli.app.load_settings", return_value=Settings()
            ) as mock_load,
            patch("cloudexit.cli.app.prepare_catalogue") as mock_dataset,
            patch("cloudexit.cli.app.handle_aws") as mock_handle,
            patch("cloudexit.cli.app.console.print"),
        ):
            app.main()

        mock_load.assert_called_once()
        self.assertIsInstance(args.settings, Settings)
        mock_dataset.assert_called_once()
        mock_handle.assert_called_once_with(args)

    def test_a_bad_setting_fails_before_the_dataset_download(self):
        with (
            patch(
                "cloudexit.cli.app.parse_arguments",
                return_value=Namespace(cloud_provider="aws"),
            ),
            patch(
                "cloudexit.cli.app.load_settings", side_effect=SettingsError("bad host")
            ),
            patch("cloudexit.cli.app.prepare_catalogue") as mock_dataset,
            patch("cloudexit.cli.app.handle_aws") as mock_handle,
            patch("cloudexit.cli.app.console.print"),
            self.assertRaises(SystemExit) as ctx,
        ):
            app.main()

        self.assertEqual(ctx.exception.code, codes.CONFIG)
        mock_dataset.assert_not_called()
        mock_handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
