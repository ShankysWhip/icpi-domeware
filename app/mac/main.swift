// Orbit for macOS: a window around the web app plus the same local helper the Windows
// version runs in Python (tile cache, DJI keychain proxy, live radar feed, file dialogs).
// The helper is a small HTTP server on 127.0.0.1; the page talks to it with a per-launch token.
//
// Test hooks: --open <file> (queue a file), --snapshot <png> [--wait s] (save the window and quit),
//             --port N, --token T, --serve-only (helper without a window, for API tests).
import Cocoa
import IOKit
import Network
import PDFKit
import WebKit

let APP = "Orbit"
let VERSION = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "2.5.1"

// The Mac's hardware UUID, for licenses locked to one computer (the page shows only a hash of it).
// The Python helper reads the same value, so a license works with either helper.
func machineID() -> String {
    let service = IOServiceGetMatchingService(mach_port_t(MACH_PORT_NULL), IOServiceMatching("IOPlatformExpertDevice"))
    defer { IOObjectRelease(service) }
    let v = IORegistryEntryCreateCFProperty(service, "IOPlatformUUID" as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue() as? String
    return v ?? "unknown"
}
let DJI_ENDPOINT = URL(string: "https://dev.dji.com/openapi/v1/flight-records/keychains")!
let USER_AGENT = "\(APP)/\(VERSION) (flight data replay desktop app)"
let TILE_SOURCES: [String: (url: String, ext: String, maxZoom: Int)] = [
    "esri-imagery": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "jpg", 19),
    "esri-labels": ("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", "png", 19),
    "esri-topo": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "jpg", 19),
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "png", 19),
    "opentopo": ("https://tile.opentopomap.org/{z}/{x}/{y}.png", "png", 17),
    "terrain": ("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png", "png", 15),
]
let PREFETCH_LIMIT = 30000
let TILE_MAX_AGE: TimeInterval = 90 * 86400

// ---------------------------------------------------------------------------- paths and small helpers
let fm = FileManager.default
let DATA: URL = {
    // ORBIT_DATA keeps tests away from the real data folder (as in the Python helper)
    let env = ProcessInfo.processInfo.environment["ORBIT_DATA"] ?? ""
    let base = env.isEmpty ? fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent(APP) : URL(fileURLWithPath: env)
    try? fm.createDirectory(at: base, withIntermediateDirectories: true)
    return base
}()
let TILE_DIR = DATA.appendingPathComponent("tiles")
let KEY_DIR = DATA.appendingPathComponent("dji-keychains")
let WEB_DIR: URL = {
    if let r = Bundle.main.resourceURL?.appendingPathComponent("web"), fm.fileExists(atPath: r.appendingPathComponent("index.html").path) { return r }
    // development: the binary sits in build/, the page in web/
    let exe = Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0]).absoluteURL
    return exe.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("web")
}()

func log(_ items: Any...) {
    let line = ISO8601DateFormatter().string(from: Date()) + " " + items.map { "\($0)" }.joined(separator: " ") + "\n"
    let url = DATA.appendingPathComponent("helper.log")
    if let h = try? FileHandle(forWritingTo: url) { h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); h.closeFile() }
    else { try? line.data(using: .utf8)!.write(to: url) }
    FileHandle.standardError.write(line.data(using: .utf8)!)
}
func jsonData(_ obj: Any) -> Data { (try? JSONSerialization.data(withJSONObject: obj, options: [])) ?? Data("{}".utf8) }
func parseJSON(_ d: Data) -> [String: Any] { ((try? JSONSerialization.jsonObject(with: d)) as? [String: Any]) ?? [:] }
func safeId(_ s: String) -> String {
    String(s.map { $0.isLetter || $0.isNumber || "_.-".contains($0) ? $0 : "_" }.prefix(120))
}

// ---------------------------------------------------------------------------- HTTP plumbing
struct Request {
    var method = "", path = "", query: [String: String] = [:], headers: [String: String] = [:], body = Data()
}
struct Response {
    var status = 200, type = "application/json", body = Data(), headers: [String: String] = [:]
    static func json(_ obj: Any, _ status: Int = 200) -> Response { Response(status: status, type: "application/json", body: jsonData(obj)) }
    static func text(_ s: String, _ status: Int) -> Response { Response(status: status, type: "text/plain", body: Data(s.utf8)) }
}
let STATUS_TEXT = [200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Server Error", 502: "Bad Gateway", 504: "Gateway Timeout"]

final class HTTPServer {
    var listener: NWListener?
    var port: UInt16 = 0
    let queue = DispatchQueue(label: "http", attributes: .concurrent)
    let handler: (Request, @escaping (Response) -> Void) -> Void
    init(handler: @escaping (Request, @escaping (Response) -> Void) -> Void) { self.handler = handler }

    func start(preferred: UInt16) -> Bool {
        var candidates: [UInt16] = Array(preferred..<(preferred + 20))
        candidates.append(0)
        for p in candidates where tryStart(port: p) { return true }
        return false
    }
    private func tryStart(port p: UInt16) -> Bool {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: p) ?? .any)
        guard let l = try? NWListener(using: params) else { return false }
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        l.stateUpdateHandler = { st in
            switch st {
            case .ready: ok = true; sem.signal()
            case .failed, .cancelled: sem.signal()
            default: break
            }
        }
        l.newConnectionHandler = { [weak self] c in self?.serve(c) }
        l.start(queue: queue)
        _ = sem.wait(timeout: .now() + 2)
        if ok, let actual = l.port?.rawValue { listener = l; port = actual; return true }
        l.cancel()
        return false
    }
    private func serve(_ c: NWConnection) {
        c.start(queue: queue)
        var buf = Data()
        func readMore() {
            c.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { data, _, done, err in
                if let d = data { buf.append(d) }
                if let req = self.parse(&buf) {
                    self.handler(req) { resp in self.send(c, resp) }
                } else if done || err != nil || buf.count > 64 << 20 {
                    c.cancel()
                } else { readMore() }
            }
        }
        readMore()
    }
    // A complete request (headers and Content-Length body) or nil when more bytes are needed.
    private func parse(_ buf: inout Data) -> Request? {
        guard let end = buf.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: buf[buf.startIndex..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        let first = lines.removeFirst().split(separator: " ")
        guard first.count >= 2 else { return nil }
        var r = Request()
        r.method = String(first[0])
        for l in lines { if let i = l.firstIndex(of: ":") { r.headers[l[..<i].lowercased()] = l[l.index(after: i)...].trimmingCharacters(in: .whitespaces) } }
        let len = Int(r.headers["content-length"] ?? "0") ?? 0
        if buf.count - (end.upperBound - buf.startIndex) < len { return nil }
        r.body = buf.subdata(in: end.upperBound..<(end.upperBound + len))
        let target = String(first[1])
        let comps = URLComponents(string: "http://x" + target)
        r.path = comps?.percentEncodedPath.removingPercentEncoding ?? target
        for item in comps?.queryItems ?? [] { r.query[item.name] = item.value ?? "" }
        return r
    }
    private func send(_ c: NWConnection, _ r: Response) {
        var head = "HTTP/1.1 \(r.status) \(STATUS_TEXT[r.status] ?? "Status")\r\nContent-Type: \(r.type)\r\nContent-Length: \(r.body.count)\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n"
        for (k, v) in r.headers { head += "\(k): \(v)\r\n" }
        head += "\r\n"
        var out = Data(head.utf8)
        out.append(r.body)
        c.send(content: out, completion: .contentProcessed { _ in c.cancel() })
    }
}

// ---------------------------------------------------------------------------- settings
enum Settings {
    static let url = DATA.appendingPathComponent("settings.json")
    static let lock = NSLock()
    static func load() -> [String: Any] { (try? Data(contentsOf: url)).map(parseJSON) ?? [:] }
    static func merge(_ patch: [String: Any]) -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        var cur = load()
        for (k, v) in patch { if v is NSNull { cur.removeValue(forKey: k) } else { cur[k] = v } }
        try? (try? JSONSerialization.data(withJSONObject: cur, options: [.prettyPrinted]))?.write(to: url, options: .atomic)
        return cur
    }
}

// ---------------------------------------------------------------------------- network state and tiles
enum Net {
    static var downUntil = Date.distantPast
    static var lastError = ""
    static var online: Bool { Date() >= downUntil }
    static func failed(_ e: Error) { downUntil = Date().addingTimeInterval(20); lastError = e.localizedDescription }
    static func ok() { downUntil = .distantPast }
}
let session: URLSession = {
    let c = URLSessionConfiguration.ephemeral
    c.requestCachePolicy = .reloadIgnoringLocalCacheData
    c.httpMaximumConnectionsPerHost = 6
    c.httpAdditionalHeaders = ["User-Agent": USER_AGENT]
    c.timeoutIntervalForRequest = 8
    return URLSession(configuration: c)
}()
func sniffType(_ d: Data) -> String {
    if d.starts(with: [0x89, 0x50, 0x4E, 0x47]) { return "image/png" }
    if d.starts(with: [0xFF, 0xD8]) { return "image/jpeg" }
    return "application/octet-stream"
}
func tileURL(_ src: String, _ z: Int, _ x: Int, _ y: Int) -> URL {
    TILE_DIR.appendingPathComponent("\(src)/\(z)/\(x)/\(y).\(TILE_SOURCES[src]!.ext)")
}
// (data, status) from the disk cache or the network
func fetchTile(_ src: String, _ z: Int, _ x: Int, _ y: Int, forceNet: Bool = false, done: @escaping (Data?, Int) -> Void) {
    let path = tileURL(src, z, x, y)
    let cached = try? Data(contentsOf: path)
    if let c = cached, !c.isEmpty {
        let age = (try? fm.attributesOfItem(atPath: path.path)[.modificationDate] as? Date).flatMap { $0 }.map { Date().timeIntervalSince($0) } ?? 0
        if age < TILE_MAX_AGE || !Net.online { return done(c, 200) }
    }
    if !Net.online && !forceNet { return done(cached, cached == nil ? 504 : 200) }
    let u = URL(string: TILE_SOURCES[src]!.url.replacingOccurrences(of: "{z}", with: "\(z)").replacingOccurrences(of: "{x}", with: "\(x)").replacingOccurrences(of: "{y}", with: "\(y)"))!
    session.dataTask(with: u) { data, resp, err in
        if let e = err { Net.failed(e); return done(cached, cached == nil ? 504 : 200) }
        Net.ok()
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 200, let d = data, !d.isEmpty {
            try? fm.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? d.write(to: path, options: .atomic)
            return done(d, 200)
        }
        done(cached, cached == nil ? (code == 403 || code == 404 ? code : 502) : 200)
    }.resume()
}
func tileRanges(_ bbox: [Double], _ zmin: Int, _ zmax: Int) -> [(z: Int, x0: Int, x1: Int, y0: Int, y1: Int)] {
    guard bbox.count == 4, zmin <= zmax else { return [] }
    var out: [(Int, Int, Int, Int, Int)] = []
    for z in zmin...zmax {
        let n = Double(1 << z)
        func tx(_ lon: Double) -> Int { Int((lon + 180) / 360 * n) }
        func ty(_ lat: Double) -> Int {
            let r = max(-85.0511, min(85.0511, lat)) * .pi / 180
            return Int((1 - log(tan(r) + 1 / cos(r)) / .pi) / 2 * n)
        }
        let x0 = max(0, tx(bbox[0])), x1 = min(Int(n) - 1, tx(bbox[2])), y0 = max(0, ty(bbox[3])), y1 = min(Int(n) - 1, ty(bbox[1]))
        if x1 >= x0 && y1 >= y0 { out.append((z, x0, x1, y0, y1)) }
    }
    return out
}
final class Prefetch {
    static let lock = NSLock()
    static var state: [String: Any] = ["running": false, "done": 0, "total": 0, "failed": 0, "cached": 0, "bytes": 0, "message": ""]
    static var cancel = false
    static func bump(_ k: String, _ by: Int = 1) { lock.lock(); state[k] = (state[k] as? Int ?? 0) + by; lock.unlock() }
    static func start(_ sources: [String], _ bbox: [Double], _ zmin: Int, _ zmax: Int) -> [String: Any] {
        var plan: [(String, Int, Int, Int, Int, Int)] = []
        for src in sources where TILE_SOURCES[src] != nil {
            var zt = min(zmax, TILE_SOURCES[src]!.maxZoom)
            if src == "osm" { zt = min(zt, 16) } // OSM's tile policy forbids bulk downloads at zoom 17+
            for r in tileRanges(bbox, zmin, zt) { plan.append((src, r.z, r.x0, r.x1, r.y0, r.y1)) }
        }
        let total = plan.reduce(0) { $0 + ($1.3 - $1.2 + 1) * ($1.5 - $1.4 + 1) }
        if total == 0 { return ["ok": false, "error": "Nothing to download for that area."] }
        if total > PREFETCH_LIMIT { return ["ok": false, "error": "That is \(total) tiles; the limit is \(PREFETCH_LIMIT). Zoom in or lower the detail.", "total": total] }
        lock.lock()
        if state["running"] as? Bool == true { lock.unlock(); return ["ok": false, "error": "A download is already running."] }
        state = ["running": true, "done": 0, "total": total, "failed": 0, "cached": 0, "bytes": 0, "message": ""]
        cancel = false
        lock.unlock()
        DispatchQueue.global().async {
            let sem = DispatchSemaphore(value: 4), group = DispatchGroup()
            outer: for (src, z, x0, x1, y0, y1) in plan {
                for x in x0...x1 { for y in y0...y1 {
                    if cancel { break outer }
                    if fm.fileExists(atPath: tileURL(src, z, x, y).path) { bump("done"); bump("cached"); continue }
                    sem.wait(); group.enter()
                    fetchTile(src, z, x, y, forceNet: true) { d, st in
                        bump("done")
                        if st == 200, let d = d { bump("bytes", d.count) } else { bump("failed") }
                        sem.signal(); group.leave()
                    }
                } }
            }
            group.wait()
            lock.lock(); state["running"] = false; state["message"] = cancel ? "cancelled" : "finished"; lock.unlock()
        }
        return ["ok": true, "total": total]
    }
}
func cacheStats() -> [String: Any] {
    var total = 0, count = 0
    var by: [String: Any] = [:]
    for src in (try? fm.contentsOfDirectory(atPath: TILE_DIR.path)) ?? [] {
        var b = 0, n = 0
        if let e = fm.enumerator(at: TILE_DIR.appendingPathComponent(src), includingPropertiesForKeys: [.fileSizeKey]) {
            for case let f as URL in e { if let s = try? f.resourceValues(forKeys: [.fileSizeKey]).fileSize { b += s; n += 1 } }
        }
        by[src] = ["bytes": b, "count": n]
        total += b; count += n
    }
    return ["bytes": total, "count": count, "bySource": by]
}

// ---------------------------------------------------------------------------- DJI keychains
func djiKeychains(_ body: [String: Any], done: @escaping (Int, [String: Any]) -> Void) {
    let key = ((body["apiKey"] as? String) ?? (Settings.load()["djiApiKey"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
    if key.isEmpty { return done(400, ["ok": false, "error": "No DJI API key set. Add it in Settings."]) }
    guard let reqBody = body["request"] as? [String: Any] else { return done(400, ["ok": false, "error": "Missing keychain request."]) }
    var r = URLRequest(url: DJI_ENDPOINT)
    r.httpMethod = "POST"
    r.timeoutInterval = 30
    r.setValue("application/json", forHTTPHeaderField: "Content-Type")
    r.setValue(key, forHTTPHeaderField: "Api-Key")
    r.httpBody = jsonData(reqBody)
    session.dataTask(with: r) { data, resp, err in
        if let e = err { Net.failed(e); return done(504, ["ok": false, "error": "Could not reach DJI (\(e.localizedDescription)). Check the internet connection."]) }
        Net.ok()
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let d = data ?? Data()
        if code != 200 {
            let msg = code == 403 ? "DJI refused the API key (HTTP 403)." : "DJI answered HTTP \(code)."
            return done(502, ["ok": false, "error": msg, "status": code, "detail": String(decoding: d.prefix(400), as: UTF8.self)])
        }
        guard let payload = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return done(502, ["ok": false, "error": "DJI sent an answer that is not JSON."]) }
        let result = payload["result"] as? [String: Any] ?? [:]
        if let c = result["code"] as? Int, c != 0 { return done(502, ["ok": false, "error": "DJI: \(result["msg"] as? String ?? "error \(c)")", "status": c]) }
        guard let kc = payload["data"] as? [Any], !kc.isEmpty else { return done(502, ["ok": false, "error": "DJI sent no keys for this log."]) }
        if let id = body["id"] as? String, !id.isEmpty {
            try? fm.createDirectory(at: KEY_DIR, withIntermediateDirectories: true)
            try? jsonData(["id": id, "saved": Date().timeIntervalSince1970, "data": kc]).write(to: KEY_DIR.appendingPathComponent(safeId(id) + ".json"))
        }
        done(200, ["ok": true, "data": kc])
    }.resume()
}

// ---------------------------------------------------------------------------- live radar feed
final class XmlSplitter {
    var buf = [UInt8]()
    func feed(_ d: Data) -> [String] {
        buf.append(contentsOf: d)
        var out: [String] = []
        while true {
            guard let lt = buf.firstIndex(of: 0x3C) else { buf.removeAll(); break }
            if lt > 0 { buf.removeFirst(lt) }
            guard buf.count > 1 else { break }
            if buf[1] == 0x3F || buf[1] == 0x21 { // <? or <!
                guard let gt = buf.firstIndex(of: 0x3E) else { break }
                buf.removeFirst(gt + 1); continue
            }
            var i = 1
            while i < buf.count, !(buf[i] == 0x20 || buf[i] == 0x3E || buf[i] == 0x2F || buf[i] == 0x0A || buf[i] == 0x0D || buf[i] == 0x09) { i += 1 }
            if i >= buf.count { break }
            if i == 1 { buf.removeFirst(); continue }
            let name = Array(buf[1..<i])
            guard let gt = buf.firstIndex(of: 0x3E) else { break }
            if buf[gt - 1] == 0x2F { out.append(String(decoding: buf[0...gt], as: UTF8.self)); buf.removeFirst(gt + 1); continue }
            let close: [UInt8] = [0x3C, 0x2F] + name + [0x3E]
            guard let r = find(close, from: gt) else { if buf.count > 16 << 20 { buf.removeAll() }; break }
            out.append(String(decoding: buf[0..<r], as: UTF8.self))
            buf.removeFirst(r)
        }
        return out
    }
    private func find(_ pat: [UInt8], from: Int) -> Int? {
        guard buf.count >= pat.count else { return nil }
        var i = from
        let last = buf.count - pat.count
        while i <= last {
            if buf[i] == pat[0] {
                var j = 1
                while j < pat.count && buf[i + j] == pat[j] { j += 1 }
                if j == pat.count { return i + pat.count }
            }
            i += 1
        }
        return nil
    }
}

final class Live {
    static let lock = NSLock()
    static var items: [(Int, Int, String)] = []
    static var seq = 0
    static var status: [String: Any] = ["state": "stopped", "message": "", "bytes": 0, "messages": 0, "peer": "", "mode": "", "record": ""]
    static var generation = 0
    static var conn: NWConnection?, listener: NWListener?, timer: DispatchSourceTimer?, record: FileHandle?
    static var peers: [NWConnection] = []
    static let q = DispatchQueue(label: "live")

    static func set(_ kv: [String: Any]) { lock.lock(); for (k, v) in kv { status[k] = v }; lock.unlock() }
    static func consume(_ sp: XmlSplitter, _ d: Data, gen: Int) {
        guard gen == generation else { return }
        lock.lock(); status["bytes"] = (status["bytes"] as? Int ?? 0) + d.count; lock.unlock()
        for m in sp.feed(d) {
            lock.lock()
            seq += 1
            items.append((seq, Int(Date().timeIntervalSince1970 * 1000), m))
            if items.count > 20000 { items.removeFirst(items.count - 20000) }
            status["messages"] = (status["messages"] as? Int ?? 0) + 1
            lock.unlock()
            record?.write(Data((m + "\n").utf8))
        }
    }
    static func poll(_ since: Int) -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        let out = items.filter { $0.0 > since }.prefix(3000).map { ["seq": $0.0, "t": $0.1, "xml": $0.2] as [String: Any] }
        return ["status": status, "seq": seq, "items": Array(out)]
    }
    static func stop() {
        generation += 1
        conn?.cancel(); conn = nil
        listener?.cancel(); listener = nil
        for p in peers { p.cancel() }
        peers = []
        timer?.cancel(); timer = nil
        try? record?.close(); record = nil
        set(["state": "stopped"])
    }
    static func start(_ cfg: [String: Any]) -> [String: Any] {
        stop()
        let mode = cfg["mode"] as? String ?? ""
        guard ["tcp-client", "tcp-server", "udp", "file"].contains(mode) else { return ["ok": false, "error": "Unknown live mode."] }
        let dir = DATA.appendingPathComponent("live")
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let df = DateFormatter(); df.dateFormat = "yyyyMMdd_HHmmss"
        let rec = dir.appendingPathComponent("live_\(df.string(from: Date())).xml")
        fm.createFile(atPath: rec.path, contents: nil)
        record = try? FileHandle(forWritingTo: rec)
        lock.lock()
        items = []
        status = ["state": "starting", "message": "", "bytes": 0, "messages": 0, "peer": "", "mode": mode, "record": rec.path]
        lock.unlock()
        let gen = generation
        let port = UInt16(truncatingIfNeeded: (cfg["port"] as? Int) ?? Int((cfg["port"] as? String) ?? "0") ?? 0)
        switch mode {
        case "tcp-client": tcpClient(host: (cfg["host"] as? String) ?? "127.0.0.1", port: port, gen: gen)
        case "tcp-server": netListener(port: port, udp: false, gen: gen)
        case "udp": netListener(port: port, udp: true, gen: gen)
        default: follow(path: (cfg["path"] as? String) ?? "", fromStart: (cfg["fromStart"] as? Bool) ?? false, gen: gen)
        }
        return ["ok": true, "record": rec.path]
    }
    static func receiveLoop(_ c: NWConnection, _ sp: XmlSplitter, gen: Int, onEnd: @escaping () -> Void) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { d, _, done, err in
            if let d = d, !d.isEmpty { consume(sp, d, gen: gen) }
            if done || err != nil || gen != generation { c.cancel(); if gen == generation { onEnd() }; return }
            receiveLoop(c, sp, gen: gen, onEnd: onEnd)
        }
    }
    static func tcpClient(host: String, port: UInt16, gen: Int) {
        guard gen == generation, let p = NWEndpoint.Port(rawValue: port) else { return }
        set(["state": "connecting", "message": "Connecting to \(host):\(port)", "peer": ""])
        let c = NWConnection(host: NWEndpoint.Host(host), port: p, using: .tcp)
        conn = c
        var retried = false
        let retry = {
            if retried || gen != generation { return }
            retried = true
            set(["state": "retrying", "message": "Not connected to \(host):\(port). Retrying."])
            q.asyncAfter(deadline: .now() + 3) { tcpClient(host: host, port: port, gen: gen) }
        }
        c.stateUpdateHandler = { st in
            switch st {
            case .ready:
                set(["state": "connected", "message": "Connected to \(host):\(port)", "peer": "\(host):\(port)"])
                receiveLoop(c, XmlSplitter(), gen: gen, onEnd: retry)
            case .failed, .waiting: c.cancel(); retry()
            default: break
            }
        }
        c.start(queue: q)
    }
    static func netListener(port: UInt16, udp: Bool, gen: Int) {
        guard let p = NWEndpoint.Port(rawValue: port), let l = try? NWListener(using: udp ? .udp : .tcp, on: p) else {
            set(["state": "error", "message": "Cannot listen on port \(port)."]); return
        }
        listener = l
        l.stateUpdateHandler = { st in
            if case .ready = st { set(["state": "listening", "message": udp ? "Listening for UDP on port \(port)" : "Waiting for the radar to connect to port \(port)"]) }
            if case .failed(let e) = st { set(["state": "error", "message": "Port \(port): \(e.localizedDescription)"]) }
        }
        l.newConnectionHandler = { c in
            peers.append(c)
            let sp = XmlSplitter()
            c.stateUpdateHandler = { st in
                if case .ready = st {
                    set(["state": "connected", "message": udp ? "Receiving UDP from \(c.endpoint)" : "Radar connected from \(c.endpoint)", "peer": "\(c.endpoint)"])
                    if udp { udpLoop(c, sp, gen: gen) } else { receiveLoop(c, sp, gen: gen) { set(["state": "listening", "message": "Radar disconnected; waiting on port \(port)", "peer": ""]) } }
                }
            }
            c.start(queue: q)
        }
        l.start(queue: q)
    }
    static func udpLoop(_ c: NWConnection, _ sp: XmlSplitter, gen: Int) {
        c.receiveMessage { d, _, _, err in
            if let d = d, !d.isEmpty { consume(sp, d, gen: gen) }
            if err == nil && gen == generation { udpLoop(c, sp, gen: gen) }
        }
    }
    static func follow(path: String, fromStart: Bool, gen: Int) {
        var pos: UInt64? = nil
        var sp = XmlSplitter()
        let t = DispatchSource.makeTimerSource(queue: q)
        t.schedule(deadline: .now(), repeating: 0.5)
        t.setEventHandler {
            guard gen == generation else { return }
            guard let size = (try? fm.attributesOfItem(atPath: path)[.size] as? NSNumber)?.uint64Value else {
                set(["state": "retrying", "message": "Waiting for the file \(path)"]); return
            }
            if pos == nil { pos = fromStart ? 0 : size; set(["state": "connected", "message": "Following \((path as NSString).lastPathComponent)", "peer": path]) }
            if size < pos! { pos = 0; sp = XmlSplitter() }
            guard size > pos!, let h = FileHandle(forReadingAtPath: path) else { return }
            h.seek(toFileOffset: pos!)
            let d = h.readData(ofLength: Int(min(size - pos!, 8 << 20)))
            h.closeFile()
            pos! += UInt64(d.count)
            consume(sp, d, gen: gen)
        }
        timer = t
        t.resume()
    }
}

// ---------------------------------------------------------------------------- report PDF
// The report arrives as fixed-size 794 x 1123 px pages stacked in one HTML page. WebKit draws it
// into one tall PDF page, which is then cut into true A4 pages (vector, text stays selectable).
final class PDFMaker: NSObject, WKNavigationDelegate {
    static var active: [PDFMaker] = []
    private var web: WKWebView?
    private var win: NSWindow?
    private var pages = 1
    private var done: ((Data?, String?) -> Void)?

    func make(html: String, pages: Int, done: @escaping (Data?, String?) -> Void) {
        self.pages = max(1, min(pages, 200))
        self.done = done
        let size = NSRect(x: 0, y: 0, width: 794, height: 1123 * CGFloat(self.pages))
        let w = WKWebView(frame: size, configuration: WKWebViewConfiguration())
        w.navigationDelegate = self
        let win = NSWindow(contentRect: size.offsetBy(dx: -40000, dy: -40000), styleMask: [.borderless], backing: .buffered, defer: false)
        win.contentView = w
        web = w
        self.win = win
        PDFMaker.active.append(self)
        w.loadHTMLString(html, baseURL: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { self.finish(nil, "timed out") }
    }
    func webView(_ w: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            let cfg = WKPDFConfiguration()
            cfg.rect = CGRect(x: 0, y: 0, width: 794, height: 1123 * CGFloat(self.pages))
            w.createPDF(configuration: cfg) { res in
                switch res {
                case .success(let data): self.finish(PDFMaker.slice(data, pages: self.pages), nil)
                case .failure(let e): self.finish(nil, e.localizedDescription)
                }
            }
        }
    }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { finish(nil, e.localizedDescription) }
    private func finish(_ d: Data?, _ err: String?) {
        guard let cb = done else { return }
        done = nil
        cb(d, d == nil ? (err ?? "could not make the PDF") : nil)
        web = nil
        win = nil
        PDFMaker.active.removeAll { $0 === self }
    }
    static func slice(_ data: Data, pages: Int) -> Data? {
        guard let doc = PDFDocument(data: data), let src = doc.page(at: 0) else { return nil }
        let box = src.bounds(for: .mediaBox)
        let pageH = box.height / CGFloat(pages)
        var a4 = CGRect(x: 0, y: 0, width: 595.28, height: 841.89)
        let s = a4.width / box.width
        let out = NSMutableData()
        guard let consumer = CGDataConsumer(data: out as CFMutableData), let ctx = CGContext(consumer: consumer, mediaBox: &a4, nil) else { return nil }
        for i in 0..<pages {
            ctx.beginPDFPage(nil)
            ctx.saveGState()
            ctx.scaleBy(x: s, y: s)
            let bottom = box.minY + box.height - CGFloat(i + 1) * pageH
            ctx.translateBy(x: -box.minX, y: -bottom)
            ctx.clip(to: CGRect(x: box.minX, y: bottom, width: box.width, height: pageH))
            src.draw(with: .mediaBox, to: ctx)
            ctx.restoreGState()
            ctx.endPDFPage()
        }
        ctx.closePDF()
        return out as Data
    }
}

// ---------------------------------------------------------------------------- the app
// Remembers the last mouse-down, so a drag that starts on the page's toolbar can move the window.
final class OrbitWebView: WKWebView {
    var lastMouseDown: NSEvent?
    override func mouseDown(with e: NSEvent) { lastMouseDown = e; super.mouseDown(with: e) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler, NSWindowDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: HTTPServer!
    var token = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    var openQueue: [String] = []
    let qlock = NSLock()
    var snapshotPath: String?
    var snapshotWait = 8.0
    var evalJS: String?
    var startJS: String?
    var serveOnly = false
    var titleObs: NSKeyValueObservation?
    var lastChrome: (bar: CGFloat, inset: CGFloat) = (0, 0)

    func applicationWillFinishLaunching(_ n: Notification) {
        var args = Array(CommandLine.arguments.dropFirst())
        var preferred: UInt16 = 47615
        while !args.isEmpty {
            let a = args.removeFirst()
            switch a {
            case "--open": if !args.isEmpty { openQueue.append(args.removeFirst()) }
            case "--snapshot": if !args.isEmpty { snapshotPath = args.removeFirst() }
            case "--wait": if !args.isEmpty { snapshotWait = Double(args.removeFirst()) ?? 8 }
            case "--eval": if !args.isEmpty { evalJS = args.removeFirst() }
            case "--js": if !args.isEmpty { startJS = args.removeFirst() }
            case "--port": if !args.isEmpty { preferred = UInt16(args.removeFirst()) ?? preferred }
            case "--token": if !args.isEmpty { token = args.removeFirst() }
            case "--serve-only": serveOnly = true
            default: if fm.fileExists(atPath: a) && !a.hasPrefix("-psn") { openQueue.append(a) }
            }
        }
        server = HTTPServer { [weak self] req, done in self?.route(req, done) ?? done(.json(["ok": false], 500)) }
        if !server.start(preferred: preferred) { log("could not start the helper"); NSApp.terminate(nil) }
        log("helper started", VERSION, "port", server.port, "web", WEB_DIR.path)
        if serveOnly { print("http://127.0.0.1:\(server.port)/?token=\(token)"); fflush(stdout) }
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        if serveOnly { NSApp.setActivationPolicy(.prohibited); return }
        let cfg = WKWebViewConfiguration()
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        cfg.websiteDataStore = .default()
        cfg.userContentController.add(self, name: "orbit")
        web = OrbitWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        // the page runs under the title bar: its own toolbar is the window's toolbar
        if web.responds(to: NSSelectorFromString("_setAutomaticallyAdjustsContentInsets:")) { web.setValue(false, forKey: "automaticallyAdjustsContentInsets") }
        let frame = NSRect(x: 0, y: 0, width: 1440, height: 900)
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = APP
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        if #available(macOS 11.0, *) { window.titlebarSeparatorStyle = .none }
        // an empty toolbar makes the title bar as tall as the page's toolbar, with the window buttons centred in it
        window.toolbar = NSToolbar(identifier: "Orbit")
        if #available(macOS 11.0, *) { window.toolbarStyle = .unified }
        window.minSize = NSSize(width: 960, height: 640)
        window.backgroundColor = NSColor(red: 0.11, green: 0.114, blue: 0.125, alpha: 1)
        window.delegate = self
        window.contentView = web
        // the window title follows the page title ("Orbit – Matrice 400 flight …")
        titleObs = web.observe(\.title, options: [.new]) { [weak self] w, _ in
            if let t = w.title, !t.isEmpty { self?.window.title = t }
        }
        window.setFrameAutosaveName("OrbitMain")
        if !window.setFrameUsingName("OrbitMain") { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(server.port)/?token=\(token)")!))
        if let p = snapshotPath {
            // test runs: keep the window visible, since macOS stops drawing windows that are hidden
            window.level = .floating
            window.orderFrontRegardless()
            DispatchQueue.main.asyncAfter(deadline: .now() + snapshotWait) {
                let shoot = {
                    self.web.takeSnapshot(with: nil) { img, _ in
                        if let img = img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
                            try? png.write(to: URL(fileURLWithPath: p))
                        }
                        NSApp.terminate(nil)
                    }
                }
                guard let js = self.evalJS else { return shoot() }
                self.web.evaluateJavaScript(js) { res, err in
                    print("EVAL:", res ?? "nil", err?.localizedDescription ?? ""); fflush(stdout)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2, execute: shoot)
                }
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { !serveOnly }

    // ---- the page's toolbar is the title bar: tell the page how tall it is and where the window buttons end,
    // and let it move and zoom the window from empty parts of the toolbar
    func pushChrome() {
        guard let w = window, let web = web else { return }
        let full = w.styleMask.contains(.fullScreen)
        var bar: CGFloat = 52, inset: CGFloat = 84
        // AppKit centres the window buttons in the title bar, so they give its height and where the toolbar may start
        if let close = w.standardWindowButton(.closeButton), let zoom = w.standardWindowButton(.zoomButton), let sv = close.superview {
            let c = sv.convert(close.frame, to: nil), z = sv.convert(zoom.frame, to: nil)
            bar = max(38, ((w.frame.height - c.midY) * 2).rounded())
            inset = (z.maxX + 14).rounded()
        }
        if bar != lastChrome.bar || inset != lastChrome.inset { lastChrome = (bar, inset); log("title bar", bar, "buttons end", inset) }
        web.evaluateJavaScript("SF.app && SF.app.setChrome({titlebar: \(Int(bar)), inset: \(Int(inset)), fullscreen: \(full)}); undefined", completionHandler: nil)
    }
    func windowDidResize(_ n: Notification) { pushChrome() }
    func windowDidEnterFullScreen(_ n: Notification) { pushChrome() }
    func windowDidExitFullScreen(_ n: Notification) { pushChrome() }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let body = m.body as? [String: Any], let type = body["type"] as? String, let w = window else { return }
        switch type {
        case "drag":
            // only while the button is still down, or the window would follow the mouse until the next click
            if NSEvent.pressedMouseButtons & 1 != 0, let e = (web as? OrbitWebView)?.lastMouseDown { w.performDrag(with: e) }
        case "zoom":
            // the user's choice in System Settings › Desktop & Dock, "Double-click a window's title bar to"
            let act = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
            if act == "Minimize" { w.performMiniaturize(nil) } else if act != "None" { w.performZoom(nil) }
        default: break
        }
    }
    func applicationWillTerminate(_ n: Notification) { Live.stop() }
    func application(_ app: NSApplication, open urls: [URL]) {
        qlock.lock(); openQueue += urls.map { $0.path }; qlock.unlock()
        window?.makeKeyAndOrderFront(nil)
    }

    // ---- menus (an Edit menu is what makes copy and paste work inside the web view)
    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(APP)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Settings…", action: #selector(showSettings(_:)), keyEquivalent: ",")
        appMenu.addItem(withTitle: "License…", action: #selector(showLicense(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(APP)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(APP)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "Open…", action: #selector(openFiles(_:)), keyEquivalent: "o")
        let imp = NSMenu(title: "Import")
        for (t, k) in [("Drone Log…", "drone"), ("Radar Data…", "radar"), ("Weather Data…", "weather")] {
            let it = imp.addItem(withTitle: t, action: #selector(importKind(_:)), keyEquivalent: "")
            it.representedObject = k
        }
        fileMenu.setSubmenu(imp, for: fileMenu.addItem(withTitle: "Import", action: nil, keyEquivalent: ""))
        fileMenu.addItem(withTitle: "Connect to Live Radar…", action: #selector(liveRadar(_:)), keyEquivalent: "")
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "Export Test Report (PDF)…", action: #selector(exportPDF(_:)), keyEquivalent: "e")
        fileMenu.addItem(withTitle: "Test Details…", action: #selector(testDetails(_:)), keyEquivalent: "i")
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        for (t, k, key) in [("Now", "now", "1"), ("Analysis", "analysis", "2"), ("Files", "files", "3")] {
            let it = view.addItem(withTitle: t, action: #selector(showTab(_:)), keyEquivalent: key)
            it.representedObject = k
        }
        view.addItem(.separator())
        view.addItem(withTitle: "2D Map", action: #selector(view2D(_:)), keyEquivalent: "")
        view.addItem(withTitle: "3D View", action: #selector(view3D(_:)), keyEquivalent: "")
        let insp = view.addItem(withTitle: "Toggle Inspector", action: #selector(toggleInspector(_:)), keyEquivalent: "i")
        insp.keyEquivalentModifierMask = [.command, .option]
        view.addItem(withTitle: "Play Intro", action: #selector(playIntro(_:)), keyEquivalent: "")
        view.addItem(.separator())
        view.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r")
        let fs = view.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fs.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = view
        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winItem.submenu = win
        NSApp.windowsMenu = win
        let helpItem = NSMenuItem(); main.addItem(helpItem)
        let help = NSMenu(title: "Help")
        help.addItem(withTitle: "Orbit Help", action: #selector(showHelp(_:)), keyEquivalent: "?")
        help.addItem(withTitle: "Keyboard Shortcuts", action: #selector(showHelp(_:)), keyEquivalent: "")
        helpItem.submenu = help
        NSApp.helpMenu = help
        NSApp.mainMenu = main
    }
    @objc func reload(_ s: Any?) { web?.reload() }
    @objc func showSettings(_ s: Any?) { callPage("SF.app.openSettings()") }
    @objc func showLicense(_ s: Any?) { callPage("SF.app.openSettings('license')") }
    @objc func liveRadar(_ s: Any?) { callPage("SF.app.openLive()") }
    @objc func view2D(_ s: Any?) { callPage("SF.app.set3D(false)") }
    @objc func view3D(_ s: Any?) { callPage("SF.app.set3D(true)") }
    @objc func toggleInspector(_ s: Any?) { callPage("SF.app.toggleInspector()") }
    @objc func playIntro(_ s: Any?) { callPage("SF.app.playIntro()") }
    @objc func importKind(_ s: NSMenuItem) { if let k = s.representedObject as? String { callPage("SF.app.pick('\(k)')") } }
    @objc func showTab(_ s: NSMenuItem) { if let k = s.representedObject as? String { callPage("SF.app.switchTab('\(k)')") } }
    private func callPage(_ js: String) { web?.evaluateJavaScript(js + "; undefined", completionHandler: nil) }
    @objc func exportPDF(_ s: Any?) { callPage("SF.app.doExport('pdf')") }
    @objc func testDetails(_ s: Any?) { callPage("SF.app.openDetails()") }
    @objc func showHelp(_ s: Any?) { callPage("SF.app.showHelp()") }
    @objc func openFiles(_ s: Any?) {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = true
        p.canChooseDirectories = false
        p.message = "Choose a drone log, radar file or weather file"
        p.beginSheetModal(for: window) { r in
            if r == .OK { self.qlock.lock(); self.openQueue += p.urls.map { $0.path }; self.qlock.unlock() }
        }
    }

    // ---- web view delegates: file inputs, alerts, external links
    func webView(_ w: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = parameters.allowsMultipleSelection
        p.canChooseDirectories = false
        p.beginSheetModal(for: window) { r in completionHandler(r == .OK ? p.urls : nil) }
    }
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.beginSheetModal(for: window) { _ in completionHandler() }
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }
    // test hook: run --js once after the first page load
    func webView(_ w: WKWebView, didFinish navigation: WKNavigation!) {
        pushChrome()
        if let js = startJS { startJS = nil; w.evaluateJavaScript(js, completionHandler: nil) }
    }
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, let h = u.host, h != "127.0.0.1", h != "localhost", u.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(u); return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }
    // If the page's process dies (for example out of memory), reload instead of leaving a blank window.
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) {
        log("web content process ended; reloading the window")
        w.load(URLRequest(url: URL(string: "http://127.0.0.1:\(server.port)/?token=\(token)&recovered=1")!))
    }
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        if let u = a.request.url { NSWorkspace.shared.open(u) }
        return nil
    }

    // ---- the helper's routes (same API as server/orbit_server.py)
    func route(_ r: Request, _ done: @escaping (Response) -> Void) {
        let host = (r.headers["host"] ?? "").lowercased()
        guard host == "127.0.0.1:\(server.port)" || host == "localhost:\(server.port)" else { return done(.json(["ok": false, "error": "bad host"], 403)) }
        if r.path.hasPrefix("/tiles/") { return tile(r, done) }
        if !r.path.hasPrefix("/api/") { return staticFile(r.path, done) }
        let tok = r.headers["x-sf-token"] ?? r.query["token"] ?? ""
        guard tok == token else { return done(.json(["ok": false, "error": "missing token"], 403)) }
        let body = r.method == "POST" ? parseJSON(r.body) : [:]
        switch (r.method, r.path) {
        case ("GET", "/api/info"):
            done(.json(["ok": true, "app": APP, "version": VERSION, "platform": "darwin", "helper": "swift", "dataDir": DATA.path, "online": Net.online, "machine": machineID(),
                        "tileSources": TILE_SOURCES.mapValues { $0.maxZoom }]))
        case ("GET", "/api/settings"): done(.json(Settings.load()))
        case ("POST", "/api/settings"): done(.json(Settings.merge(body)))
        case ("GET", "/api/net"): done(.json(["online": Net.online, "lastError": Net.lastError]))
        case ("POST", "/api/ping"): done(.json(["ok": true, "online": Net.online]))
        case ("GET", "/api/dji/keychains"):
            let u = KEY_DIR.appendingPathComponent(safeId(r.query["id"] ?? "") + ".json")
            if let d = try? Data(contentsOf: u), let data = parseJSON(d)["data"] { done(.json(["ok": true, "data": data, "cached": true])) } else { done(.json(["ok": false], 404)) }
        case ("POST", "/api/dji/keychains"): djiKeychains(body) { st, out in done(.json(out, st)) }
        case ("GET", "/api/tiles/prefetch"): Prefetch.lock.lock(); let s = Prefetch.state; Prefetch.lock.unlock(); done(.json(s))
        case ("POST", "/api/tiles/prefetch"):
            let bbox = (body["bbox"] as? [Any] ?? []).compactMap { ($0 as? NSNumber)?.doubleValue }
            done(.json(Prefetch.start(body["sources"] as? [String] ?? ["esri-imagery"], bbox, (body["minZoom"] as? Int) ?? 10, (body["maxZoom"] as? Int) ?? 17)))
        case ("POST", "/api/tiles/prefetch/cancel"): Prefetch.cancel = true; done(.json(["ok": true]))
        case ("GET", "/api/tiles/stats"): DispatchQueue.global().async { done(.json(cacheStats())) }
        case ("POST", "/api/tiles/clear"): try? fm.removeItem(at: TILE_DIR); done(.json(["ok": true]))
        case ("GET", "/api/file"):
            let p = r.query["path"] ?? ""
            guard let d = fm.contents(atPath: p) else { return done(.json(["ok": false, "error": "File not found: \(p)"], 404)) }
            done(Response(status: 200, type: "application/octet-stream", body: d, headers: ["X-File-Size": "\(d.count)"]))
        case ("GET", "/api/open-queue"):
            qlock.lock(); let paths = openQueue; openQueue = []; qlock.unlock()
            done(.json(["paths": paths]))
        case ("POST", "/api/open-queue"):
            qlock.lock(); openQueue += (body["paths"] as? [String] ?? []); qlock.unlock()
            done(.json(["ok": true]))
        case ("POST", "/api/open-dialog"):
            DispatchQueue.main.async {
                let p = NSOpenPanel()
                p.allowsMultipleSelection = (body["multiple"] as? Bool) ?? false
                p.message = body["title"] as? String ?? "Open"
                let finish: (NSApplication.ModalResponse) -> Void = { res in done(.json(["ok": true, "paths": res == .OK ? p.urls.map { $0.path } : []])) }
                if let w = self.window { p.beginSheetModal(for: w, completionHandler: finish) } else { finish(p.runModal()) }
            }
        case ("POST", "/api/save"):
            let name = r.query["name"] ?? "export.dat"
            let data = r.body
            DispatchQueue.main.async {
                let p = NSSavePanel()
                p.nameFieldStringValue = name
                p.canCreateDirectories = true
                let finish: (NSApplication.ModalResponse) -> Void = { res in
                    guard res == .OK, let u = p.url else { return done(.json(["ok": false, "cancelled": true])) }
                    do { try data.write(to: u); done(.json(["ok": true, "path": u.path])) } catch { done(.json(["ok": false, "error": error.localizedDescription], 500)) }
                }
                if let w = self.window { p.beginSheetModal(for: w, completionHandler: finish) } else { finish(p.runModal()) }
            }
        case ("POST", "/api/pdf"):
            let html = String(decoding: r.body, as: UTF8.self)
            let pages = Int(r.query["pages"] ?? "1") ?? 1
            let name = r.query["name"] ?? "Orbit report.pdf"
            DispatchQueue.main.async {
                let write: (URL) -> Void = { url in
                    PDFMaker().make(html: html, pages: pages) { data, err in
                        guard let data = data else { return done(.json(["ok": false, "error": err ?? "could not make the PDF"], 500)) }
                        do { try data.write(to: url); done(.json(["ok": true, "path": url.path, "bytes": data.count])) } catch { done(.json(["ok": false, "error": error.localizedDescription], 500)) }
                    }
                }
                if let p = r.query["path"], !p.isEmpty { return write(URL(fileURLWithPath: p)) }
                let panel = NSSavePanel()
                panel.nameFieldStringValue = name
                panel.allowedFileTypes = ["pdf"]
                panel.canCreateDirectories = true
                let finish: (NSApplication.ModalResponse) -> Void = { res in
                    guard res == .OK, let u = panel.url else { return done(.json(["ok": false, "cancelled": true])) }
                    write(u)
                }
                if let w = self.window { panel.beginSheetModal(for: w, completionHandler: finish) } else { finish(panel.runModal()) }
            }
        case ("POST", "/api/open-file"):
            let p = body["path"] as? String ?? ""
            DispatchQueue.main.async { NSWorkspace.shared.open(URL(fileURLWithPath: p)) }
            done(.json(["ok": fm.fileExists(atPath: p)]))
        case ("POST", "/api/reveal"):
            let p = body["path"] as? String ?? ""
            DispatchQueue.main.async { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)]) }
            done(.json(["ok": true]))
        case ("POST", "/api/live/start"): done(.json(Live.start(body)))
        case ("POST", "/api/live/stop"): Live.stop(); done(.json(["ok": true]))
        case ("GET", "/api/live/poll"): done(.json(Live.poll(Int(r.query["since"] ?? "0") ?? 0)))
        case ("POST", "/api/quit"): done(.json(["ok": true])); DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
        default: done(.json(["ok": false, "error": "unknown endpoint"], 404))
        }
    }
    func staticFile(_ path: String, _ done: (Response) -> Void) {
        var p = path == "/" || path.isEmpty ? "/index.html" : path
        if p.contains("..") { p = "/" }
        let u = WEB_DIR.appendingPathComponent(String(p.dropFirst())).standardized
        guard u.path.hasPrefix(WEB_DIR.standardized.path + "/"), let d = fm.contents(atPath: u.path) else { return done(.text("not found", 404)) }
        let types = ["html": "text/html; charset=utf-8", "js": "application/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "svg": "image/svg+xml", "png": "image/png", "json": "application/json", "txt": "text/plain; charset=utf-8", "ico": "image/x-icon"]
        let ext = u.pathExtension.lowercased()
        done(Response(status: 200, type: types[ext] ?? "application/octet-stream", body: d, headers: ["Cache-Control": "no-cache"]))
    }
    func tile(_ r: Request, _ done: @escaping (Response) -> Void) {
        let parts = r.path.split(separator: "/").map(String.init) // tiles, src, z, x, y
        guard parts.count == 5, TILE_SOURCES[parts[1]] != nil, let z = Int(parts[2]), let x = Int(parts[3]), let y = Int(parts[4].split(separator: ".")[0]),
              z <= TILE_SOURCES[parts[1]]!.maxZoom, x < (1 << z), y < (1 << z) else { return done(.text("", 404)) }
        fetchTile(parts[1], z, x, y) { d, st in
            if st == 200, let d = d { done(Response(status: 200, type: sniffType(d), body: d, headers: ["Cache-Control": "max-age=3600"])) }
            else { done(Response(status: st, type: "text/plain", body: Data(), headers: ["Cache-Control": "no-store"])) }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
