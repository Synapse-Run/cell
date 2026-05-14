#[no_mangle]
extern "C" {
    fn snapshot();
    fn restore();
    fn print(i: i32);
}

static mut STATE: i32 = 0;

#[no_mangle]
pub extern "C" fn run() {
    unsafe {
        STATE += 1;
        print(STATE);
        if STATE == 1 {
            snapshot();
            STATE += 100;
            print(STATE);
        } else {
            // Second run?
            restore();
            print(STATE);
        }
    }
}
