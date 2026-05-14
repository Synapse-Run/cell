use wasmtime::*;
use std::fs;

fn main() -> Result<()> {
    let mut config = Config::new();
    config.wasm_simd(true);
    let engine = Engine::new(&config)?;
    
    // We'll write a small rust program that counts, snapshots itself, and then we exit.
    // Then we run it again, it restores itself, and prints the old count!
    Ok(())
}
