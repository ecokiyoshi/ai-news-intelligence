const assert = require("assert");
const { validateEditPlan, safeAsset } = require("../src/validation");
const { resolveAsset } = require("../src/timeline-builder");
const plan = { schema: "ai-news-intelligence/premiere-edit-plan", schema_version: 1,
  sequence: { aspect_ratio: "16:9", duration_seconds: 2 },
  track_roles: { "video.scene": {}, "dialogue.sabisuke": {}, "dialogue.haru": {}, "captions.overlay": {} },
  scenes: [{ scene_index: 0, start_seconds: 0, duration_seconds: 2, image: { asset: "scene.png" }, dialogue: [] }]
};
assert.strictEqual(validateEditPlan(plan), plan);
assert.throws(() => safeAsset("../secret"), /traversal/);
assert.strictEqual(resolveAsset("C:\\runs\\x", "audio/001.mp3"), "C:/runs/x/audio/001.mp3");
console.log("UXP static validation passed");
