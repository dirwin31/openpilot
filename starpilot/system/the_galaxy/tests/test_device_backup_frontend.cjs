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
const finishes = [];
const prompts = [];
const ctx = {
  GalaxySection: {}, GxNotice: {},
  GalaxyConfirm: async options => { prompts.push(options); return options.dismissible === false ? downloadModels : accepted; },
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
  assert.equal(prompts.at(-1).confirmLabel, 'Download Models and Reboot');
  assert.equal(prompts.at(-1).cancelLabel, 'Reboot Without Downloading');
  assert.equal(prompts.at(-1).dismissible, false, 'no Later/backdrop dismissal');
  assert.equal(view.deviceRestoreReady, false);
  downloadModels = true;
  await view.onDeviceRestoreFile(event());
  assert.deepEqual(finishes, [false, true]);
  assert.equal(view.deviceBackupBusy, 'models', 'poll until model downloads finish');
  status = {stage:'error', message:'Model unavailable', models:[{key:'a'}]};
  await view.loadDeviceRestoreStatus();
  assert.equal(view.deviceRestoreReady, true);
  assert.equal(view.deviceBackupError, true);
  assert.equal(view.deviceBackupBusy, '');
  view.isOnroad = true;
  await view.rebootAfterRestore();
  assert.equal(finishes.length, 2);
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
  const section = ctx.view.template.split('title="Backup & Restore"')[1].split('</GalaxySection>')[0];
  assert.match(section, /@click="backupDevice"/);
  assert.match(section, /@change="onDeviceRestoreFile"/);
  assert.ok(!section.includes('href="/device_backup"'));
  const calls = [];
  const network = {FormData, fetch:async (url, options) => {calls.push({url, options}); return {ok:true, json:async()=>({success:true})};}};
  vm.createContext(network);
  vm.runInContext(fs.readFileSync(path.join(base, 'api.js'), 'utf8').replace(/export /g, '') + '\nthis.client=api;', network);
  await network.client.restoreDevice(new Blob(['zip']));
  assert.equal(calls[0].url, '/api/device_backup/restore');
  assert.ok(calls[0].options.body instanceof FormData);
  assert.ok(calls[0].options.body.get('backup'));
  assert.equal(calls[0].options.headers, undefined);
  await network.client.rebootAfterDeviceRestore(true);
  assert.equal(calls[1].url, '/api/device_backup/reboot');
  assert.deepEqual(JSON.parse(calls[1].options.body), {downloadModels:true});
  await network.client.rebootAfterDeviceRestore(false);
  assert.deepEqual(JSON.parse(calls[2].options.body), {downloadModels:false});
  const modalSource = fs.readFileSync(path.join(base,'components/GalaxyModal.js'),'utf8');
  assert.match(modalSource, /@click.self="dismissible && cancel\(\)"/);
  console.log('Inline restore choices, both reboot paths, progress polling, retry, guards and upload passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
