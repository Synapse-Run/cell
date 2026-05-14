import re

with open("src/cell_api.rs", "r") as f:
    content = f.read()

# We need to insert rate_limits state inside run_cell_api, before the loop
insert_pos = content.find("for stream in listener.incoming() {")
if insert_pos == -1:
    print("Cannot find run_cell_api loop")
    exit(1)

state_init = """
    let mut ip_counts: std::collections::HashMap<String, (u64, std::time::Instant)> = std::collections::HashMap::new();
    let max_reqs_per_sec = 20;

"""

content = content[:insert_pos] + state_init + content[insert_pos:]

# Now inside the `keep-alive` loop, after checking CORS and static pages, but before health/demo endpoints
# Actually, the best place is right after parsing the request, before processing anything else.
req_process_pos = content.find("let wants_keepalive = req.headers")
if req_process_pos == -1:
    print("Cannot find keepalive processing")
    exit(1)

rate_limit_logic = """
                    // Rate limiting logic based on IP (X-Forwarded-For or Peer Addr)
                    let client_ip = req.headers.get("x-forwarded-for")
                        .map(|s| s.split(',').next().unwrap_or("").trim().to_string())
                        .unwrap_or_else(|| stream.peer_addr().map(|a| a.ip().to_string()).unwrap_or_default());
                    
                    if !client_ip.is_empty() {
                        let now = std::time::Instant::now();
                        let entry = ip_counts.entry(client_ip.clone()).or_insert((0, now));
                        if now.duration_since(entry.1).as_secs() >= 1 {
                            entry.0 = 1;
                            entry.1 = now;
                        } else {
                            entry.0 += 1;
                            if entry.0 > max_reqs_per_sec {
                                send_json_keepalive(&mut stream, 429, r#"{"error":"rate_limit_exceeded"}"#, false);
                                break;
                            }
                        }
                    }

"""

content = content[:req_process_pos] + rate_limit_logic + content[req_process_pos:]

with open("src/cell_api.rs", "w") as f:
    f.write(content)

print("Patched with rate limiting")
