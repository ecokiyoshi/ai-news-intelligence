(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.EditPlanValidation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const ROLES = ["video.scene", "dialogue.sabisuke", "dialogue.haru", "captions.overlay"];
  function assert(condition, message) { if (!condition) throw new Error(message); }
  function safeAsset(value) {
    assert(typeof value === "string" && value.length > 0, "Asset path is missing.");
    assert(!/^(?:[a-z]+:|[\\/])/i.test(value), "Asset paths must be relative to the edit plan.");
    assert(!value.split(/[\\/]/).includes(".."), "Asset path traversal is not allowed.");
  }
  function validateEditPlan(plan) {
    assert(plan && typeof plan === "object", "Edit plan must be an object.");
    assert(plan.schema === "ai-news-intelligence/premiere-edit-plan", "Unsupported edit-plan schema.");
    assert(plan.schema_version === 1, "Unsupported edit-plan version.");
    assert(plan.sequence && plan.sequence.aspect_ratio === "16:9", "Sequence must be 16:9.");
    assert(Number.isFinite(plan.sequence.duration_seconds) && plan.sequence.duration_seconds > 0, "Sequence duration is invalid.");
    ROLES.forEach(role => assert(plan.track_roles && plan.track_roles[role], `Missing track role: ${role}`));
    assert(Array.isArray(plan.scenes) && plan.scenes.length > 0, "Scenes are missing.");
    plan.scenes.forEach((scene, index) => {
      assert(scene.scene_index === index, "Scene indexes must be sequential.");
      assert(Number.isFinite(scene.start_seconds) && Number.isFinite(scene.duration_seconds) && scene.duration_seconds > 0, `Scene ${index} timing is invalid.`);
      safeAsset(scene.image && scene.image.asset);
      assert(Array.isArray(scene.dialogue), `Scene ${index} dialogue is invalid.`);
      scene.dialogue.forEach(segment => {
        assert(["sabisuke", "haru"].includes(segment.speaker), "Unsupported dialogue speaker.");
        assert(segment.track_role === `dialogue.${segment.speaker}`, "Speaker track role mismatch.");
        if (segment.asset !== null) safeAsset(segment.asset);
      });
    });
    return plan;
  }
  return { validateEditPlan, safeAsset };
});
