"""Check orchestration call contracts without rendering or downloading models."""
import ast
import inspect
import textwrap

from server.wizard import WizardRunner


def test_direct_start_supplies_required_finish_arguments():
    tree = ast.parse(textwrap.dedent(inspect.getsource(WizardRunner._run)))
    signature = inspect.signature(WizardRunner._finish)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
             and node.func.attr == "_finish"]
    assert calls
    for call in calls:
        assert not call.args
        assert all(keyword.arg is not None for keyword in call.keywords)
        signature.bind(None, **{keyword.arg: None for keyword in call.keywords})
