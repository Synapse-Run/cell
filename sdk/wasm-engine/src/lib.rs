use wasm_bindgen::prelude::*;
use sha2::{Sha256, Digest};
use std::collections::hash_map::DefaultHasher;
use std::hash::Hasher;

const D: usize = 512;
const K: usize = 25;
const ALPHA: i32 = 39;
const BETA: i32 = 2;
const THRESH: i32 = 16_384;

#[wasm_bindgen]
pub struct SovereignEngine {
    weights: Vec<u64>,  // 512 * 16 = 8192 u64s (64 KiB)
    votes: Vec<i32>,    // 512 * 512 = 262144 i32s (1 MB)
    ltp: u64,
    ltd: u64,
    rng_state: u64,
    last_receipt: String,
}

#[wasm_bindgen]
impl SovereignEngine {
    #[wasm_bindgen(constructor)]
    pub fn new(seed: u64) -> SovereignEngine {
        let mut engine = SovereignEngine {
            weights: vec![0u64; D * 16],
            votes: vec![0i32; D * D],
            ltp: 0,
            ltd: 0,
            rng_state: seed.wrapping_add(0x1337BEEF),
            last_receipt: String::new(),
        };
        engine.init_weights();
        engine
    }

    fn sm64(s: &mut u64) -> u64 { 
        *s = s.wrapping_add(0x9E3779B97F4A7C15); 
        let mut z = *s; 
        z = (z^(z>>30)).wrapping_mul(0xBF58476D1CE4E5B9); 
        z = (z^(z>>27)).wrapping_mul(0x94D049BB133111EB); 
        z^(z>>31) 
    }

    fn init_weights(&mut self) {
        let mut rng = self.rng_state;
        for n in 0..D { 
            let b = n * 16;
            let mut wp = [0u64; 8]; let mut wn = [0u64; 8];
            let mut pp = 0; let mut pn = 0;
            while pp < K { 
                let z = Self::sm64(&mut rng); let p = (z&511) as usize; let m = 1u64<<(p&63);
                if wp[p>>6]&m==0 && wn[p>>6]&m==0 { wp[p>>6]|=m; pp+=1; } 
            }
            while pn < K { 
                let z = Self::sm64(&mut rng); let p = (z&511) as usize; let m = 1u64<<(p&63);
                if wp[p>>6]&m==0 && wn[p>>6]&m==0 { wn[p>>6]|=m; pn+=1; } 
            }
            for j in 0..8 { self.weights[b+j] = wp[j]; self.weights[b+8+j] = wn[j]; }
        }
        self.rng_state = rng;
    }

    // Input is 8 u64s (512 bits)
    fn forward_assoc(&self, input: &[u64; 8], output: &mut [u64; 8]) {
        let mut volts = [0i16; D];
        for n in 0..D {
            let nb = n * 16;
            let mut v: i16 = 0;
            for j in 0..8 { v += (input[j] & self.weights[nb + j]).count_ones() as i16; }
            for j in 0..8 { v -= (input[j] & self.weights[nb + 8 + j]).count_ones() as i16; }
            volts[n] = v;
        }
        let hmin = -25i16; let hmax = 25i16; let hrange = (hmax - hmin + 1) as usize;
        let mut counts = [0u16; 51];
        for &v in &volts {
            let b = (v - hmin).clamp(0, hrange as i16 - 1) as usize;
            counts[b] += 1;
        }
        let mut run = 0u16; let mut thr = hmax;
        for b in (0..hrange).rev() {
            run += counts[b];
            if run >= K as u16 { thr = b as i16 + hmin; break; }
        }
        *output = [0u64; 8]; let mut act = 0;
        for n in 0..D {
            if volts[n] > thr && act < K {
                output[n >> 6] |= 1u64 << (n & 63);
                act += 1;
            }
        }
        if act < K {
            for n in 0..D {
                if volts[n] == thr && act < K {
                    let m = 1u64 << (n & 63);
                    if output[n >> 6] & m == 0 {
                        output[n >> 6] |= m;
                        act += 1;
                    }
                }
            }
        }
    }

    fn flip_up(&mut self, post: usize, pre: usize) {
        let nb = post * 16;
        let word = pre >> 6; let bit = 1u64 << (pre & 63);
        if self.weights[nb + 8 + word] & bit != 0 { self.weights[nb + 8 + word] &= !bit; }
        else { self.weights[nb + word] |= bit; }
    }

    fn flip_down(&mut self, post: usize, pre: usize) {
        let nb = post * 16;
        let word = pre >> 6; let bit = 1u64 << (pre & 63);
        if self.weights[nb + word] & bit != 0 { self.weights[nb + word] &= !bit; }
        else { self.weights[nb + 8 + word] |= bit; }
    }

    fn apply_plasticity(&mut self, pre_idx: &[u16; K], tgt_idx: &[u16; K]) {
        for &pi in pre_idx {
            let row = (pi as usize) * D;
            for j in 0..D { self.votes[row + j] = self.votes[row + j].saturating_sub(BETA); }
            let bonus = ALPHA + BETA;
            for &tj in tgt_idx { self.votes[row + tj as usize] = self.votes[row + tj as usize].saturating_add(bonus); }
            for j in 0..D {
                let v = self.votes[row + j];
                let noise = ((Self::sm64(&mut self.rng_state) & 1023) as i32) - 512;
                let noisy_v = v.saturating_add(noise);
                if noisy_v >= THRESH {
                    self.flip_up(j, pi as usize);
                    self.votes[row + j] = -(THRESH / 4);
                    self.ltp += 1;
                } else if noisy_v <= -THRESH {
                    self.flip_down(j, pi as usize);
                    self.votes[row + j] = THRESH / 4;
                    self.ltd += 1;
                }
            }
        }
    }

    // Takes JS Uint8Array (512 bytes, treating each as 0 or 1) 
    // Returns JS Uint8Array (512 bytes)
    // Computes prediction, applies plasticity to match target, generates receipt.
    pub fn step(&mut self, current_input: &[u8], target_input: &[u8]) -> js_sys::Uint8Array {
        let mut curr = [0u64; 8];
        let mut tgt = [0u64; 8];
        
        // Pack Uint8Array into u64s
        for i in 0..512 {
            if i < current_input.len() && current_input[i] > 0 {
                curr[i >> 6] |= 1u64 << (i & 63);
            }
            if i < target_input.len() && target_input[i] > 0 {
                tgt[i >> 6] |= 1u64 << (i & 63);
            }
        }

        let mut out = [0u64; 8];
        self.forward_assoc(&curr, &mut out);

        // Compute plasticity
        let mut ctx_idx = [0u16; K];
        let mut tgt_idx = [0u16; K];
        let mut c_i = 0; let mut t_i = 0;
        for i in 0..512 {
            if (curr[i >> 6] & (1u64 << (i & 63))) != 0 && c_i < K { ctx_idx[c_i] = i as u16; c_i += 1; }
            if (tgt[i >> 6] & (1u64 << (i & 63))) != 0 && t_i < K { tgt_idx[t_i] = i as u16; t_i += 1; }
        }
        self.apply_plasticity(&ctx_idx, &tgt_idx);

        // Generate Audit Receipt
        let mut hasher = Sha256::new();
        hasher.update(bytemuck::cast_slice(&curr));
        hasher.update(bytemuck::cast_slice(&self.weights));
        hasher.update(bytemuck::cast_slice(&out));
        let result = hasher.finalize();
        self.last_receipt = hex::encode(result);

        // Unpack prediction to Uint8Array
        let mut out_bytes = vec![0u8; 512];
        for i in 0..512 {
            if (out[i >> 6] & (1u64 << (i & 63))) != 0 {
                out_bytes[i] = 1;
            }
        }

        js_sys::Uint8Array::from(out_bytes.as_slice())
    }

    pub fn get_last_receipt(&self) -> String {
        self.last_receipt.clone()
    }

    pub fn get_overlap(&self, a: &[u8], b: &[u8]) -> u32 {
        let mut overlap = 0;
        for i in 0..std::cmp::min(a.len(), b.len()) {
            if a[i] > 0 && b[i] > 0 {
                overlap += 1;
            }
        }
        overlap
    }
}
