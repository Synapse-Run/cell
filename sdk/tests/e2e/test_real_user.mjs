/**
 * Synapse E2E Proof — current preview gateway surface.
 *
 * Usage: node test_real_user.mjs
 */
import { Synapse } from '@runsynapse/sdk';

const API_URL = process.env.SYNAPSE_URL || 'https://api.synapserun.dev';
const API_KEY = process.env.SYNAPSE_API_KEY || 'test_key_placeholder';

const client = new Synapse({ apiKey: API_KEY, baseUrl: API_URL });

let passed = 0;
let failed = 0;

function ok(test, msg) { passed++; console.log(`  ✅ ${test}: ${msg}`); }
function fail(test, msg) { failed++; console.log(`  ❌ ${test}: ${msg}`); }

async function main() {
  console.log('============================================');
  console.log('Synapse E2E Proof — Real User Experience');
  console.log(`  API: ${API_URL}`);
  console.log(`  SDK: @runsynapse/sdk@0.1.0 (from npm)`);
  console.log('============================================\n');

  // 1. Health check
  console.log('[1/5] Health Check');
  try {
    const h = await client.health();
    if (h.status === 'ok' || h.status === 'healthy') ok('health', JSON.stringify(h));
    else fail('health', JSON.stringify(h));
  } catch (e) { fail('health', e.message); }

  // 2. Execute — canonical @f syntax (from CODEX)
  console.log('\n[2/5] Execute (@f canonical syntax)');
  try {
    const t0 = performance.now();
    const r = await client.execute('@f 0 main\n42');
    const rtt = (performance.now() - t0).toFixed(0);
    if (r.result === 42) ok('execute_canonical', `result=42, server=${r.latencyMs}ms, rtt=${rtt}ms`);
    else fail('execute_canonical', `expected 42, got ${r.result} (full: ${JSON.stringify(r)})`);
  } catch (e) { fail('execute_canonical', e.message); }

  // 3. Execute — fn sugar syntax
  console.log('\n[3/5] Execute (fn sugar syntax)');
  try {
    const t0 = performance.now();
    const r = await client.execute('fn main() -> i32 [ret 42]');
    const rtt = (performance.now() - t0).toFixed(0);
    if (r.result === 42) ok('execute_fn', `result=42, server=${r.latencyMs}ms, rtt=${rtt}ms`);
    else fail('execute_fn', `expected 42, got ${r.result} (full: ${JSON.stringify(r)})`);
  } catch (e) { fail('execute_fn', e.message); }

  // 4. Execute — computation with prefix notation
  console.log('\n[4/5] Execute restricted Python');
  try {
    const t0 = performance.now();
    const r = await client.executePython('result = 21 + 21');
    const rtt = (performance.now() - t0).toFixed(0);
    if (r.result === 42) ok('execute_python', `result=42, server=${r.latencyMs}ms, rtt=${rtt}ms`);
    else fail('execute_python', `expected 42, got ${r.result}`);
  } catch (e) { fail('execute_python', e.message); }

  // 5. Structured compile failure
  console.log('\n[5/5] Structured compile failure');
  try {
    await client.execute('');
    fail('compile_failed', 'expected structured client error');
  } catch (e) {
    if (String(e.message).includes('empty_code')) ok('compile_failed', e.message);
    else fail('compile_failed', e.message);
  }

  // Summary
  console.log('\n============================================');
  console.log(`Results: ${passed} passed, ${failed} failed`);
  if (failed === 0) console.log('ALL TESTS PASSED — current preview surface is coherent');
  console.log('============================================');
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });
