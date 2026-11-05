// orbit-license: makes signed Orbit license codes. Only the person who sells or gives out Orbit uses it.
//
//   orbit-license keygen            create the secret signing key (once; never replaced)
//   orbit-license pubkey            print the public key that goes into web/js/license.js
//   orbit-license make --name "Name" [--org "Company"] [--email a@b] [--expires YYYY-MM-DD]
//                      [--machine XXXX-XXXX-XXXX-XXXX] [--out folder]
//
// The secret key lives in ~/Library/Application Support/Orbit License Maker/private.key (readable by
// this user only). A license is "ORBIT1-" + base64url(JSON) + "." + base64url(ECDSA P-256 signature of
// that JSON), which Orbit checks offline with the public key. Back the key up: without it no new
// licenses can be made for Orbit builds that carry its public key.
import CryptoKit
import Foundation

let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Orbit License Maker")
let keyURL = dir.appendingPathComponent("private.key")

func fail(_ s: String) -> Never {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
    exit(1)
}
func b64url(_ d: Data) -> String {
    d.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}
func loadKey() -> P256.Signing.PrivateKey {
    guard let s = try? String(contentsOf: keyURL, encoding: .utf8),
          let d = Data(base64Encoded: s.trimmingCharacters(in: .whitespacesAndNewlines)),
          let k = try? P256.Signing.PrivateKey(rawRepresentation: d) else { fail("No signing key at \(keyURL.path). Run: orbit-license keygen") }
    return k
}
func isoDay(_ d: Date) -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyy-MM-dd"
    return f.string(from: d)
}

var args = Array(CommandLine.arguments.dropFirst())
let cmd = args.isEmpty ? "" : args.removeFirst()
switch cmd {
case "keygen":
    if FileManager.default.fileExists(atPath: keyURL.path) { fail("A signing key already exists at \(keyURL.path); it was not replaced.") }
    do {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let k = P256.Signing.PrivateKey()
        try k.rawRepresentation.base64EncodedString().write(to: keyURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: keyURL.path)
        print(k.publicKey.x963Representation.base64EncodedString())
    } catch { fail("Could not create the key: \(error)") }
case "pubkey":
    print(loadKey().publicKey.x963Representation.base64EncodedString())
case "make":
    var o: [String: String] = [:]
    while !args.isEmpty {
        let a = args.removeFirst()
        if a.hasPrefix("--"), !args.isEmpty { o[String(a.dropFirst(2))] = args.removeFirst().trimmingCharacters(in: .whitespacesAndNewlines) }
    }
    guard let name = o["name"], !name.isEmpty else { fail("The person's name is needed (--name).") }
    var id = Data(count: 4)
    _ = id.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 4, $0.baseAddress!) }
    var p: [String: Any] = ["v": 1, "id": id.map { String(format: "%02x", $0) }.joined(), "n": name, "i": isoDay(Date())]
    if let v = o["org"], !v.isEmpty { p["o"] = v }
    if let v = o["email"], !v.isEmpty { p["e"] = v }
    if let v = o["expires"], !v.isEmpty {
        guard v.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil else { fail("The end date must look like 2027-12-31.") }
        p["x"] = v
    }
    if let v = o["machine"], !v.isEmpty {
        let m = v.uppercased()
        guard m.range(of: #"^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$"#, options: .regularExpression) != nil else { fail("The computer ID must look like 1A2B-3C4D-5E6F-7A8B (Orbit shows it when it asks for a license).") }
        p["m"] = m
    }
    guard let json = try? JSONSerialization.data(withJSONObject: p, options: [.sortedKeys]),
          let sig = try? loadKey().signature(for: json) else { fail("Could not sign the license.") }
    let code = "ORBIT1-" + b64url(json) + "." + b64url(sig.rawRepresentation)
    let out = URL(fileURLWithPath: o["out"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Desktop").path)
    let safe = name.replacingOccurrences(of: #"[/\\:*?"<>|]"#, with: "-", options: .regularExpression)
    let file = out.appendingPathComponent("Orbit license - \(safe).orbitlicense")
    var text = "Orbit license\n\nLicensed to: \(name)\n"
    if let v = p["o"] as? String { text += "Organisation: \(v)\n" }
    text += "Valid until: " + ((p["x"] as? String) ?? "no end date") + "\n"
    text += "Computer: " + ((p["m"] as? String) ?? "any") + "\n"
    text += "License ID: \(p["id"]!)\n\nOpen this file with Orbit, or copy the code below into Orbit's license window.\n\n\(code)\n"
    do { try text.write(to: file, atomically: true, encoding: .utf8) } catch { fail("Could not save \(file.path): \(error)") }
    print(file.path)
    print(code)
default:
    fail("Usage: orbit-license keygen | pubkey | make --name \"Name\" [--org ..] [--email ..] [--expires YYYY-MM-DD] [--machine ID] [--out folder]")
}
