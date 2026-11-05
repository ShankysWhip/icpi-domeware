// Draws Orbit's icon (same design as web/img/icon.svg) at one size.
// Usage: make_icon <pixels> <out.png> [--flat]   (--flat: no macOS margin, for Windows and web icons)
import Cocoa

let args = CommandLine.arguments
guard args.count >= 3, let px = Int(args[1]) else {
    FileHandle.standardError.write("usage: make_icon <pixels> <out.png> [--flat]\n".data(using: .utf8)!)
    exit(2)
}
let flat = args.contains("--flat")
let s = CGFloat(px)
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                           hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext

// the macOS icon grid leaves about a tenth of the canvas around the tile
let inset = flat ? 0 : (px >= 64 ? s * 0.098 : s * 0.04)
let tile = CGRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
let k = tile.width / 64
// SVG units (64 x 64, y down) to pixels
func P(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: tile.minX + x * k, y: tile.maxY - y * k) }
func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor { CGColor(red: r / 255, green: g / 255, blue: b / 255, alpha: a) }
let small = px <= 32

// graphite tile, lighter at the top
let corner = 14 * k
let tilePath = CGPath(roundedRect: tile, cornerWidth: corner, cornerHeight: corner, transform: nil)
ctx.saveGState()
ctx.addPath(tilePath)
ctx.clip()
let bg = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: [rgb(47, 49, 54), rgb(19, 20, 23)] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(bg, start: CGPoint(x: 0, y: tile.maxY), end: CGPoint(x: 0, y: tile.minY), options: [])
ctx.restoreGState()
if !small {
    let edge = tile.insetBy(dx: 0.5 * k, dy: 0.5 * k)
    ctx.addPath(CGPath(roundedRect: edge, cornerWidth: corner - 0.5 * k, cornerHeight: corner - 0.5 * k, transform: nil))
    ctx.setStrokeColor(rgb(255, 255, 255, 0.08))
    ctx.setLineWidth(max(1, k))
    ctx.strokePath()
}
ctx.setLineCap(.round)
ctx.setLineJoin(.round)
let white = rgb(255, 255, 255)
let orange = rgb(255, 122, 26)

// faint radar range rings round the centre
if !small {
    ctx.setLineWidth(1.3 * k)
    for (r, a) in [(CGFloat(11.5), CGFloat(0.1)), (21.5, 0.07)] {
        ctx.setStrokeColor(rgb(255, 255, 255, a))
        let c = P(32, 32)
        ctx.strokeEllipse(in: CGRect(x: c.x - r * k, y: c.y - r * k, width: 2 * r * k, height: 2 * r * k))
    }
}
// the orbit: an ellipse tilted 24°, its far half dimmer than its near half
let rx: CGFloat = 22, ry: CGFloat = 9.5, tilt = -24 * CGFloat.pi / 180
func orbit(_ th: CGFloat) -> CGPoint {
    let dx = rx * cos(th), dy = ry * sin(th)
    return P(32 + dx * cos(tilt) - dy * sin(tilt), 32 + dx * sin(tilt) + dy * cos(tilt))
}
func half(from a: CGFloat, alpha: CGFloat, width: CGFloat) {
    ctx.setStrokeColor(rgb(255, 255, 255, alpha))
    ctx.setLineWidth(width * k)
    ctx.move(to: orbit(a))
    for i in 1...96 { ctx.addLine(to: orbit(a + CGFloat.pi * CGFloat(i) / 96)) }
    ctx.strokePath()
}
half(from: CGFloat.pi, alpha: small ? 0.5 : 0.36, width: small ? 3.4 : 2.4)
half(from: 0, alpha: 1, width: small ? 3.6 : 2.6)
// the radar at the centre and the drone on the near side of the orbit
let c = P(32, 32), cr = (small ? 4.4 : 3.3) * k
ctx.setFillColor(white)
ctx.fillEllipse(in: CGRect(x: c.x - cr, y: c.y - cr, width: 2 * cr, height: 2 * cr))
let d = P(51.3, 28.6), gap = (small ? 8.6 : 6.7) * k, dr = (small ? 6.2 : 4.5) * k
ctx.setFillColor(rgb(34, 36, 40))
ctx.fillEllipse(in: CGRect(x: d.x - gap, y: d.y - gap, width: 2 * gap, height: 2 * gap))
ctx.setFillColor(orange)
ctx.fillEllipse(in: CGRect(x: d.x - dr, y: d.y - dr, width: 2 * dr, height: 2 * dr))

NSGraphicsContext.restoreGraphicsState()
let png = rep.representation(using: .png, properties: [:])!
try! png.write(to: URL(fileURLWithPath: args[2]))
