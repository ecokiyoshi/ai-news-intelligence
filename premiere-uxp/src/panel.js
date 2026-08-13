const { localFileSystem, fileTypes } = require("uxp").storage;
let loaded = null;
let entry = null;
const status = document.querySelector("#status");
const buttons = { validate: document.querySelector("#validate"), build: document.querySelector("#build"), rebuild: document.querySelector("#rebuild") };

function report(message, type = "muted") { status.className = type; status.textContent = message; }
function directoryOf(nativePath) { return nativePath.replace(/[\\/][^\\/]+$/, ""); }
async function validate() {
  if (!loaded) throw new Error("Select an edit plan first.");
  EditPlanValidation.validateEditPlan(loaded);
  report(`${loaded.run.title}\n${loaded.scenes.length} scenes · ${loaded.sequence.duration_seconds}s\nPlan is valid.`, "ok");
  buttons.build.disabled = false;
  buttons.rebuild.disabled = false;
}
document.querySelector("#load").addEventListener("click", async () => {
  try {
    entry = await localFileSystem.getFileForOpening({ types: fileTypes.json, allowMultiple: false });
    if (!entry) return;
    loaded = JSON.parse(await entry.read());
    buttons.validate.disabled = false;
    buttons.build.disabled = true;
    buttons.rebuild.disabled = true;
    await validate();
  } catch (error) { report(error.message, "error"); }
});
buttons.validate.addEventListener("click", async () => { try { await validate(); } catch (error) { report(error.message, "error"); } });
async function build(rebuild) {
  try {
    await validate();
    if (rebuild && !confirm(`Delete and rebuild only the generated sequence “${loaded.sequence.name}”?`)) return;
    report("Building timeline…");
    const result = await PremiereTimelineBuilder.buildTimeline(loaded, directoryOf(entry.nativePath), { rebuild });
    report(`Built ${result.sequence.name}.\n${result.overlayCount} overlay entries remain available as plan sidecar metadata.`, "ok");
  } catch (error) { report(error.message, "error"); }
}
buttons.build.addEventListener("click", () => build(false));
buttons.rebuild.addEventListener("click", () => build(true));
