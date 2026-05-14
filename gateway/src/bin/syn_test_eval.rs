use wasmtime::*;
use std::io::Read;

struct State {
    dummy_rx: Vec<u8>,
    mailbox_in: sha2::Sha256,
    mailbox_out: sha2::Sha256,
}

fn main() {
    let mut args = std::env::args().skip(1);
    let wasm_file = args.next().unwrap();
    let mut wasm_bytes = Vec::new();
    std::fs::File::open(&wasm_file).unwrap().read_to_end(&mut wasm_bytes).unwrap();

    // Read dummy_rx from stdin
    let mut dummy_rx = Vec::new();
    std::io::stdin().read_to_end(&mut dummy_rx).unwrap();

    let engine = Engine::default();
    let module = Module::new(&engine, &wasm_bytes).unwrap();
    use sha2::Digest;
    let state = State {
        dummy_rx,
        mailbox_in: sha2::Sha256::new(),
        mailbox_out: sha2::Sha256::new(),
    };

    let mut store = Store::new(&engine, state);
    let mut linker = Linker::new(&engine);

    linker.func_wrap("env", "p2p_receive", |mut caller: Caller<'_, State>, ptr: i64, max_len: i64| -> i64 {
        let max = max_len as usize;
        let p = ptr as usize;
        let mut msg = caller.data_mut().dummy_rx.clone();
        if msg.len() > max { msg.truncate(max); }
        caller.data_mut().dummy_rx.clear(); // pop

        if let Some(mem) = caller.get_export("memory").and_then(|e| e.into_memory()) {
            mem.write(&mut caller, p, &msg).unwrap();
        }
        caller.data_mut().mailbox_in.update(&msg);
        msg.len() as i64
    }).unwrap();

    linker.func_wrap("env", "p2p_request", |mut caller: Caller<'_, State>, peer: i64, ptr: i64, len: i64| -> i64 {
        let p = ptr as usize;
        let l = len as usize;
        let mut data = vec![0u8; l];
        if let Some(mem) = caller.get_export("memory").and_then(|e| e.into_memory()) {
            mem.read(&caller, p, &mut data).unwrap();
        }
        caller.data_mut().mailbox_out.update(&data);
        0
    }).unwrap();

    linker.func_wrap("env", "p2p_broadcast", |mut caller: Caller<'_, State>, ptr: i64, len: i64| -> i64 {
        let p = ptr as usize;
        let l = len as usize;
        let mut data = vec![0u8; l];
        if let Some(mem) = caller.get_export("memory").and_then(|e| e.into_memory()) {
            mem.read(&caller, p, &mut data).unwrap();
        }
        caller.data_mut().mailbox_out.update(&data);
        0
    }).unwrap();

    linker.define_unknown_imports_as_traps(&module).unwrap();

    let instance = linker.instantiate(&mut store, &module).unwrap();
    let main_fn = instance.get_typed_func::<(), i64>(&mut store, "main").unwrap();
    let res = main_fn.call(&mut store, ()).unwrap();

    let state = store.into_data();
    let hash_in = hex::encode(state.mailbox_in.finalize());
    let hash_out = hex::encode(state.mailbox_out.finalize());
    
    println!("RESULT: {}", res);
    println!("MAILBOX_IN: {}", hash_in);
    println!("MAILBOX_OUT: {}", hash_out);
}
