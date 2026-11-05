need('util', 'aes');
const hex = (s) => Uint8Array.from(s.match(/../g).map((h) => parseInt(h, 16)));
const toHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
// FIPS-197 appendix C.3
const dk = SF.aes.decKey(hex('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f'));
const p = SF.aes.decryptBlock(dk, hex('8ea2b7ca516745bfeafc49904b496089'));
print('fips c.3', toHex(p) === '00112233445566778899aabbccddeeff' ? 'PASS' : 'FAIL ' + toHex(p));
// openssl CBC with PKCS#7: 76 bytes, five blocks once padded (made with OpenSSL 3.5 through Node's crypto)
const pt = hex('4f72626974207465737420766563746f723a204145532d3235362d434243207769746820504b435323372070616464696e672c206669766520626c6f636b73206f6e6365207061646465642e');
const ct = hex('d5dbf3f815121171444212f95d301343bb72720a7f22ef2ec94ce1e9910475cb78daaa6907e245aa6e2c32726a4b325fbfdae517a4404a4d9af843fd203a7ae779b911128fb6eda837c378da6588f1cd');
const out = SF.aes.cbcDecrypt(SF.aes.decKey(hex('e49a8aed27eb575f8ac40c80a6adbf84641ba7fb4ce55f70e490f9d57f92c439')), hex('f3e101c5aa97a68548506a4267b2e4b2'), ct);
print('openssl cbc', out && toHex(out) === toHex(pt) ? 'PASS' : 'FAIL');
let t = Date.now(); const big = new Uint8Array(1 << 20); for (let i = 0; i < 4; i++) SF.aes.cbcDecrypt(dk, hex('000102030405060708090a0b0c0d0e0f'), big, true);
print('speed MB/s', (4 / ((Date.now() - t) / 1000)).toFixed(1));
done(Promise.resolve());
