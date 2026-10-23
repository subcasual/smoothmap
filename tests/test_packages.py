"""The repository's packages depend on each other in one direction only, so each can be used (and
published) on its own: this checks every import in each package against what it may use."""
import ast
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# package -> what it may import besides the standard library and itself
ALLOWED = {
    "lz4block": {"lz4"},                  # python-lz4, optional
    "redbundle": {"lz4block"},
    "swf_patch": {"yaml"},
    "redswf_patch": {"swf_patch"},
    "ffdec_utils": set(),                 # runs FFDec as a program
    "patch_generator": {"swf_patch", "ffdec_utils", "yaml"},
}


def imports(package):
    """(file, top-level module) for every absolute import in the package."""
    for directory, _, files in os.walk(os.path.join(ROOT, package)):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        modules = [node.module]
                    else:
                        continue
                    for module in modules:
                        yield os.path.relpath(path, ROOT), module.split(".")[0]


@unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+ to tell the standard library apart")
class Dependencies(unittest.TestCase):
    def test_imports_follow_the_layering(self):
        for package, allowed in ALLOWED.items():
            allowed = allowed | {package, "__future__"} | set(sys.stdlib_module_names)
            stray = [f"{path} imports {module}" for path, module in imports(package) if module not in allowed]
            self.assertEqual(stray, [], package)


if __name__ == "__main__":
    unittest.main()
