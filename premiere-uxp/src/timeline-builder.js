(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PremiereTimelineBuilder = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normalize(path) { return path.replace(/\\/g, "/").replace(/\/$/, ""); }
  function resolveAsset(planDirectory, relative) { return `${normalize(planDirectory)}/${relative}`; }

  async function findOrImport(ppro, project, path, targetBin) {
    let matches = await ppro.ClipProjectItem.findItemsMatchingMediaPath(path, true);
    if (!matches.length) {
      const ok = await project.importFiles([path], true, targetBin, false);
      if (!ok) throw new Error(`Premiere could not import: ${path}`);
      matches = await ppro.ClipProjectItem.findItemsMatchingMediaPath(path, true);
    }
    if (!matches.length) throw new Error(`Imported media was not found: ${path}`);
    return matches[0];
  }

  async function buildTimeline(plan, planDirectory, options) {
    const ppro = options && options.ppro ? options.ppro : require("premierepro");
    const rebuild = Boolean(options && options.rebuild);
    const project = await ppro.Project.getActiveProject();
    if (!project) throw new Error("Open a Premiere project before building the timeline.");
    const existing = (await project.getSequences()).find(sequence => sequence.name === plan.sequence.name);
    if (existing && !rebuild) throw new Error(`Generated sequence already exists: ${plan.sequence.name}. Use Rebuild explicitly.`);
    if (existing && rebuild) {
      const removed = await project.deleteSequence(existing);
      if (!removed) throw new Error("Premiere refused to remove the generated sequence.");
    }
    const targetBin = await project.getInsertionBin();
    const uniquePaths = new Set();
    plan.scenes.forEach(scene => {
      uniquePaths.add(scene.image.asset);
      scene.dialogue.forEach(segment => { if (segment.asset) uniquePaths.add(segment.asset); });
    });
    const items = new Map();
    for (const relative of uniquePaths) {
      const absolute = resolveAsset(planDirectory, relative);
      items.set(relative, await findOrImport(ppro, project, absolute, targetBin));
    }
    const sequence = await project.createSequence(plan.sequence.name, "");
    if (!sequence) throw new Error("Premiere could not create the target sequence.");
    const editor = ppro.SequenceEditor.getEditor(sequence);
    const actions = [];
    plan.scenes.forEach(scene => {
      const time = ppro.TickTime.createWithSeconds(scene.start_seconds);
      const image = items.get(scene.image.asset);
      actions.push(image.createSetInOutPointsAction(
        ppro.TickTime.createWithSeconds(0),
        ppro.TickTime.createWithSeconds(scene.duration_seconds)
      ));
      actions.push(editor.createInsertProjectItemAction(image, time, plan.track_roles["video.scene"].preferred_index, 0, false));
      scene.dialogue.forEach(segment => {
        if (!segment.asset) return;
        const segmentTime = ppro.TickTime.createWithSeconds(segment.start_seconds);
        const audioTrack = plan.track_roles[segment.track_role].preferred_index;
        actions.push(editor.createInsertProjectItemAction(items.get(segment.asset), segmentTime, 0, audioTrack, false));
      });
    });
    const committed = project.executeTransaction(compound => actions.forEach(action => compound.addAction(action)), `Build ${plan.sequence.name}`);
    if (!committed) throw new Error("Premiere rejected the timeline transaction.");
    await project.setActiveSequence(sequence);
    await project.openSequence(sequence);
    return { sequence, captionsMode: plan.captions.mode, overlayCount: plan.scenes.reduce((sum, scene) => sum + scene.overlay_text.length, 0) };
  }
  return { buildTimeline, resolveAsset };
});
