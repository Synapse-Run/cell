use wasmtime::*;

fn main() -> Result<()> {
    let engine = Engine::default();
    let module = Module::new(&engine, r#"(module (memory (export "m") 1) (func (export "f") (result i32) i32.const 42))"#)?;
    let mut store = Store::new(&engine, ());
    let instance = Instance::new(&mut store, &module, &[])?;
    
    let mem = instance.get_memory(&mut store, "m").unwrap();
    println!("Memory size: {}", mem.data_size(&store));
    Ok(())
}
