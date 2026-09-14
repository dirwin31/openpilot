const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.resolve(__dirname, '../assets/mobile/js');
let accepted = false;
let downloadModels = false;
let restores = 0;
let backups = 0;
let restoreFails = false;
let finishFails = false;
let status = {stage:'idle'};
let confirmNoDownload = [];
const finishes = [];
const prompts = [];
const ctx = {
  GalaxySection: {}, GxNotice: {},
  downloadBlob: () => {},
  GalaxyConfirm: async options => {
    prompts.push(options);
    if (options.confirmLabel === 'Reboot Now') return confirmNoDownload.length ? confirmNoDownload.shift() : true;
    return options.dismissible === false ? downloadModels : accepted;
  },
  api: {
    restoreDevice: async () => {
      restores++;
      if (restoreFails) throw new Error('Invalid backup');
      return {success:true, message:'Settings restored', models:[{key:'a', standard:true, lab:false}]};
    },
    rebootAfterDeviceRestore: async download => {
      finishes.push(download);
      if (finishFails) throw new Error('Network unavailable');
      return {success:true, stage:download ? 'downloading' : 'rebooting', message:'Finishing restore'};
    },
    deviceRestoreStatus: async () => status,
    backupDevice: async () => { backups++; throw new Error('Not enough space'); },
  },
};
vm.createContext(ctx);
const source = fs.readFileSync(path.join(base, 'views/SystemTools.js'), 'utf8')
  .replace(/^import[^\n]+\n/gm, '').replace('export const SystemTools', 'const SystemTools');
vm.runInContext(source + '\nthis.view = SystemTools;', ctx);
(async () => {
  const view = {...ctx.view.data(), ...ctx.view.methods, loadProfiles: async () => {}};
  const event = () => ({target:{files:[{name:'backup.zip'}], value:'backup.zip'}});
  await view.onDeviceRestoreFile(event());
  assert.equal(restores, 0, 'cancel before restore must not upload');
  accepted = true;
  view.isOnroad = true;
  await view.onDeviceRestoreFile(event());
  await view.backupDevice();
  assert.equal(restores, 0);
  assert.equal(backups, 0);
  view.isOnroad = false;
  await view.onDeviceRestoreFile(event());
  assert.deepEqual(finishes, [false], 'secondary action must reboot without downloading');
  assert.equal(prompts.at(-2).confirmLabel, 'Download Models and Reboot');
  assert.equal(prompts.at(-2).cancelLabel, 'Reboot Without Downloading');
  assert.equal(prompts.at(-2).dismissible, false, 'no Later/backdrop dismissal');
  assert.equal(prompts.at(-1).confirmLabel, 'Reboot Now', 'rebooting without downloads needs its own confirmation');
  assert.equal(prompts.at(-1).dismissible, false);
  confirmNoDownload = [false, true];
  view.deviceRestoreReady = true;
  const beforeBack = prompts.length;
  await view.rebootAfterRestore();
  assert.deepEqual(prompts.slice(beforeBack).map(prompt => prompt.confirmLabel),
    ['Download Models and Reboot', 'Reboot Now', 'Download Models and Reboot', 'Reboot Now'], 'Back returns to the choice');
  assert.deepEqual(finishes, [false, false]);
  assert.equal(view.deviceRestoreReady, false);
  downloadModels = true;
  await view.onDeviceRestoreFile(event());
  assert.deepEqual(finishes, [false, false, true]);
  assert.equal(view.deviceBackupBusy, 'models', 'poll until model downloads finish');
  status = {stage:'error', message:'Model unavailable', models:[{key:'a'}]};
  await view.loadDeviceRestoreStatus();
  assert.equal(view.deviceRestoreReady, true);
  assert.equal(view.deviceBackupError, true);
  assert.equal(view.deviceBackupBusy, '');
  view.isOnroad = true;
  await view.rebootAfterRestore();
  assert.equal(finishes.length, 3);
  view.isOnroad = false;
  finishFails = true;
  await view.rebootAfterRestore();
  assert.equal(view.deviceRestoreReady, true, 'connection failure can be retried');
  assert.match(view.deviceBackupMessage, /final step could not be confirmed/);
  finishFails = false;
  downloadModels = false;
  await view.rebootAfterRestore();
  assert.equal(finishes.at(-1), false);
  restoreFails = true;
  const promptCount = prompts.length;
  const finishCount = finishes.length;
  await view.onDeviceRestoreFile(event());
  assert.equal(prompts.length, promptCount + 1, 'failed restore must not offer final choices');
  assert.equal(finishes.length, finishCount);
  await view.backupDevice();
  assert.equal(view.deviceBackupMessage, 'Not enough space');
  // Reports found when the page opens: a finished restore, an automatic rollback, or a pending/failed rollback.
  for (const [stage, error] of [['complete', false], ['rolled_back', false], ['restore_error', true]]) {
    const before = prompts.length;
    status = {stage, message:`${stage} message`};
    await view.loadDeviceRestoreStatus({prompt:true});
    assert.equal(view.deviceBackupMessage, `${stage} message`);
    assert.equal(view.deviceBackupError, error, `${stage} error styling`);
    assert.equal(view.deviceRestoreReady, false, `${stage} must not offer the reboot choice`);
    assert.equal(view.deviceBackupBusy, '');
    assert.equal(prompts.length, before, `${stage} must not prompt`);
  }
  const section = ctx.view.template.split('title="Backup & Restore"')[1].split('</GalaxySection>')[0];
  assert.match(section, /@click="backupDevice"/);
  assert.match(section, /@change="onDeviceRestoreFile"/);
  assert.ok(!section.includes('href="/device_backup"'));
  const calls = [];
  const network = {FormData, fetch:async (url, options) => {calls.push({url, options}); return {ok:true, json:async()=>({success:true})};}};
  vm.createContext(network);
  vm.runInContext(fs.readFileSync(path.join(base, 'api.js'), 'utf8').replace(/export /g, '') + '\nthis.client=api;', network);
  const zip = new Blob(['zip']);
  await network.client.restoreDevice(zip);
  assert.equal(calls[0].url, '/api/device_backup/restore');
  assert.equal(calls[0].options.body, zip, 'raw body so the server streams it to /data');
  assert.equal(calls[0].options.headers['Content-Type'], 'application/zip');
  await network.client.rebootAfterDeviceRestore(true);
  assert.equal(calls[1].url, '/api/device_backup/reboot');
  assert.deepEqual(JSON.parse(calls[1].options.body), {downloadModels:true});
  await network.client.rebootAfterDeviceRestore(false);
  assert.deepEqual(JSON.parse(calls[2].options.body), {downloadModels:false});
  const modalSource = fs.readFileSync(path.join(base,'components/GalaxyModal.js'),'utf8');
  assert.match(modalSource, /@click.self="dismissible && cancel\(\)"/);
  console.log('Inline restore choices, confirmed reboot paths, progress polling, retry, guards and raw upload passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
