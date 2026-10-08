"""Execute the fresh-project 360 UI path without any saved setup."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_fresh_project_spherical_state_exists_before_continue():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend runtime regression")
    app = Path(__file__).parents[1] / "web" / "app.js"
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
function section(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
const panel = {hidden: false, open: false};
let applied;
const context = vm.createContext({
  window: {}, document: {querySelector: () => panel},
  hasSphericalInput: () => true,
  renderSphericalSourceOptions: () => {},
  selectedSphericalSourcePath: () => "camera360.mp4",
  applySphericalSetup: values => { applied = values; },
});
vm.runInContext('"use strict";\n' +
  section("const detected =", "let progressStartedAt") +
  section("function normalizeSphericalSetup", "function applySphericalSetup") +
  section("function resetSphericalSetupToGlobal", "function hasSphericalInput") +
  section("function renderSphericalSetup()", "async function saveSphericalSetup"), context);
// Continue with a 360 file, before any reset or saved project has initialized it.
vm.runInContext('selectedPlatform = "youtube"; renderSphericalSetup();', context);
assert.strictEqual(JSON.stringify(applied), "{}");
// Opening a saved project must still use its own camera profile.
vm.runInContext('resetSphericalSetupToGlobal({settings:{spherical_landmarks_by_source:{"camera360.mp4":{singer:{yaw:42}}}}});', context);
assert.strictEqual(applied.singer.yaw, 42);
// New Project must clear the previous project's profile and restore global defaults.
vm.runInContext('sphericalProjectSettings = null; appConfig.spherical_landmarks = {singer:{yaw:7}}; resetSphericalSetupToGlobal();', context);
assert.strictEqual(applied.singer.yaw, 7);
vm.runInContext('appConfig.spherical_landmarks = {}; resetSphericalSetupToGlobal();', context);
assert.strictEqual(JSON.stringify(applied), "{}");
'''
    subprocess.run([node, "-e", script, str(app)], check=True, capture_output=True, text=True)
