const assert = require("assert");
const { extendSceneStills } = require("../src/timeline-builder");

function clipAt(start, actions) {
  return {
    getStartTime: async () => ({ seconds: start }),
    createSetEndAction: time => {
      const action = { start, end: time.seconds };
      actions.push(action);
      return action;
    },
  };
}

(async () => {
  const actions = [];
  const ppro = {
    Constants: { TrackItemType: { CLIP: 1 } },
    TickTime: { createWithSeconds: seconds => ({ seconds }) },
  };
  const track = {
    getTrackItems: (type, includeEmpty) => {
      assert.strictEqual(type, 1);
      assert.strictEqual(includeEmpty, false);
      return [clipAt(0, actions), clipAt(12.5, actions)];
    },
  };
  const sequence = { getVideoTrack: async index => {
    assert.strictEqual(index, 0);
    return track;
  } };
  const project = {
    lockedAccess: callback => callback(),
    executeTransaction: callback => {
      callback({ addAction: () => {} });
      return true;
    },
  };
  const plan = {
    sequence: { name: "AINI-test", frame_rate: 30 },
    track_roles: { "video.scene": { preferred_index: 0 } },
    scenes: [
      { start_seconds: 0, duration_seconds: 12.5 },
      { start_seconds: 12.5, duration_seconds: 3.25 },
    ],
  };

  await extendSceneStills(ppro, project, sequence, plan);
  assert.deepStrictEqual(actions, [
    { start: 0, end: 12.5 },
    { start: 12.5, end: 15.75 },
  ]);

  const missingSequence = { getVideoTrack: async () => ({ getTrackItems: () => [clipAt(1, [])] }) };
  await assert.rejects(
    extendSceneStills(ppro, project, missingSequence, plan),
    /Inserted scene clip was not found at 0s/,
  );

  console.log("UXP still-duration validation passed");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
