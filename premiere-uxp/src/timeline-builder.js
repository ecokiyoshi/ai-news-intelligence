(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PremiereTimelineBuilder = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function normalize(path) { return path.replace(/\\/g, "/").replace(/\/$/, ""); }
  function resolveAsset(planDirectory, relative) { return `${normalize(planDirectory)}/${relative}`; }

  function samePath(left, right) {
    return normalize(left).toLowerCase() === normalize(right).toLowerCase();
  }

  async function findMediaInFolder(ppro, folder, path) {
    const children = await folder.getItems();
    for (const item of children) {
      try {
        const clip = ppro.ClipProjectItem.cast(item);
        if (samePath(await clip.getMediaFilePath(), path)) return clip;
      } catch (_) {
        // Folder items cannot be cast to ClipProjectItem.
      }
      try {
        const childFolder = ppro.FolderItem.cast(item);
        const match = await findMediaInFolder(ppro, childFolder, path);
        if (match) return match;
      } catch (_) {
        // Clip items cannot be cast to FolderItem.
      }
    }
    return null;
  }

  async function findOrImport(ppro, project, path, targetBin) {
    let match = await findMediaInFolder(ppro, await project.getRootItem(), path);
    if (!match) {
      const ok = await project.importFiles([path], true, targetBin, false);
      if (!ok) throw new Error(`Premiere could not import: ${path}`);
      match = await findMediaInFolder(ppro, await project.getRootItem(), path);
    }
    if (!match) throw new Error(`Imported media was not found: ${path}`);
    return match;
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
    let committed = false;
    project.lockedAccess(() => {
      const editor = ppro.SequenceEditor.getEditor(sequence);
      committed = project.executeTransaction(
        compound => {
          plan.scenes.forEach(scene => {
            const time = ppro.TickTime.createWithSeconds(scene.start_seconds);
            const image = items.get(scene.image.asset);
            compound.addAction(editor.createInsertProjectItemAction(
              ppro.ProjectItem.cast(image), time, plan.track_roles["video.scene"].preferred_index, 0, false
            ));
            scene.dialogue.forEach(segment => {
              if (!segment.asset) return;
              const segmentTime = ppro.TickTime.createWithSeconds(segment.start_seconds);
              const audioTrack = plan.track_roles[segment.track_role].preferred_index;
              compound.addAction(editor.createInsertProjectItemAction(
                ppro.ProjectItem.cast(items.get(segment.asset)), segmentTime, 0, audioTrack, false
              ));
            });
          });
        },
        `Build ${plan.sequence.name}`
      );
    });
    if (!committed) throw new Error("Premiere rejected the timeline transaction.");
    await project.setActiveSequence(sequence);
    await project.openSequence(sequence);
    return { sequence, captionsMode: plan.captions.mode, overlayCount: plan.scenes.reduce((sum, scene) => sum + scene.overlay_text.length, 0) };
  }
  return { buildTimeline, resolveAsset };
});
