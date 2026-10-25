import argparse
import re
import unittest
from unittest.mock import patch

from cloudexit.cli.parser import parse_arguments
from cloudexit.settings import ENV_VARS
from tests.support.paths import ROOT

MARKDOWN = ("README.md", "CHANGELOG.md", "docs/migration-2.0.md")
INVOCATION = re.compile(
    r"(?<![\w/.-])(?:cloudexit|python3? main\.py)(?![\w.-])((?:[ \t]+[^\s]+)*)"
)
STOP = (">", ">>", "|", "&&", "2>&1", "")


class Captured(Exception):
    pass


def built_parser():
    # parse_arguments() builds the parser and parses in one go, so take the
    # parser it would have used rather than changing the CLI for a test
    holder = []

    def capture(self, *args, **kwargs):
        holder.append(self)
        raise Captured

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            parse_arguments()
        except Captured:
            pass
    return holder[0]


def surface(parser, flags, commands, path=()):
    for action in parser._actions:
        flags.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                commands.add((*path, name))
                surface(subparser, flags, commands, (*path, name))


def text_of(name):
    return (ROOT / name).read_text(encoding="utf-8")


def code_fragments(name):
    body = text_of(name).replace("\\\n", " ")
    yield from re.findall(r"```[a-z]*\n(.*?)```", body, re.S)
    yield from re.findall(r"`([^`\n]+)`", body)


def invocations():
    for name in MARKDOWN:
        for fragment in code_fragments(name):
            for match in INVOCATION.finditer(fragment):
                tokens = []
                for token in match.group(1).split():
                    token = token.strip("[]`'\",")
                    if token in STOP or token.startswith(">"):
                        break
                    tokens.append(token)
                if tokens:
                    yield name, tokens


class DocumentedCommandTests(unittest.TestCase):
    def setUp(self):
        self.flags, self.commands = set(), set()
        surface(built_parser(), self.flags, self.commands)

    def test_the_parser_surface_was_read(self):
        self.assertIn(("aws",), self.commands)
        self.assertIn(("spool", "flush"), self.commands)
        self.assertIn("--upload", self.flags)

    def test_every_documented_flag_exists(self):
        unknown = {
            f"{name}: {token}"
            for name, tokens in invocations()
            for token in tokens
            if token.startswith("--") and token not in self.flags
        }
        self.assertEqual(sorted(unknown), [])

    def test_every_documented_subcommand_exists(self):
        unknown = set()
        for name, tokens in invocations():
            words = [token for token in tokens if not token.startswith("-")]
            if words and (words[0],) not in self.commands:
                unknown.add(f"{name}: {words[0]}")
        self.assertEqual(sorted(unknown), [])

    def test_every_documented_environment_variable_exists(self):
        known = set(ENV_VARS.values())
        unknown = {
            f"{name}: {found}"
            for name in MARKDOWN
            for found in re.findall(r"CLOUDEXIT_[A-Z_]+", text_of(name))
            if found not in known
        }
        self.assertEqual(sorted(unknown), [])


def slug(heading):
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s+", "-", text.strip())


def anchors(name):
    found, fenced = set(), False
    for line in text_of(name).splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            found.add(slug(line.lstrip("#")))
    return found


class MarkdownLinkTests(unittest.TestCase):
    LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

    def links(self):
        for name in MARKDOWN:
            for target in self.LINK.findall(text_of(name)):
                if not target.startswith(("http://", "https://", "mailto:", "#")):
                    yield name, target
                elif target.startswith("#"):
                    yield name, target

    def test_every_relative_link_points_at_something_in_the_repository(self):
        missing = []
        for name, target in self.links():
            path = target.split("#", 1)[0]
            if path and not ((ROOT / name).parent / path).exists():
                missing.append(f"{name} -> {target}")
        self.assertEqual(missing, [])

    def test_every_anchor_exists_in_the_file_it_points_at(self):
        missing = []
        for name, target in self.links():
            path, _, anchor = target.partition("#")
            if not anchor:
                continue
            if path:
                resolved = ((ROOT / name).parent / path).resolve()
                if resolved.suffix != ".md" or not resolved.is_file():
                    continue
                target_name = resolved.relative_to(ROOT).as_posix()
            else:
                target_name = name
            if anchor not in anchors(target_name):
                missing.append(f"{name} -> {target}")
        self.assertEqual(missing, [])


class ReadmeStateTests(unittest.TestCase):
    def test_nothing_is_marked_alpha_any_more(self):
        for name in MARKDOWN:
            with self.subTest(name):
                self.assertNotIn("(alpha)", text_of(name))

    def test_the_moved_reference_block_is_not_in_the_readme(self):
        body = text_of("README.md")
        for heading in (
            "## Platform settings",
            "## Catalogue dataset",
            "## Sync document",
            "## Reports and uploads",
            "### Retries and the spool",
        ):
            with self.subTest(heading):
                self.assertNotIn(heading, body)

    def test_the_readme_points_at_the_documentation_and_the_migration_guide(self):
        body = text_of("README.md")
        self.assertIn("## Documentation", body)
        self.assertIn("./docs/migration-2.0.md", body)
        self.assertIn("./CHANGELOG.md", body)


class ExampleConfigTests(unittest.TestCase):
    PLACEHOLDERS = {
        "config/aws_example.json": "AKIAIOSFODNN7EXAMPLE",
        "config/azure_example.json": "00000000-0000-0000-0000-000000000000",
    }

    def test_the_examples_carry_obvious_placeholders(self):
        for name, expected in self.PLACEHOLDERS.items():
            with self.subTest(name):
                self.assertIn(expected, text_of(name))

    def test_no_example_config_holds_a_secret(self):
        for name in self.PLACEHOLDERS:
            with self.subTest(name):
                body = text_of(name)
                self.assertNotRegex(body, r'"secretKey"\s*:\s*"[^"]')
                self.assertNotRegex(body, r'"clientSecret"\s*:\s*"[^"]')


if __name__ == "__main__":
    unittest.main()
