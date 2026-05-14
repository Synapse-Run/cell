export default {
    join: (...args) => args.join('/'),
    resolve: (...args) => args.join('/'),
    dirname: (p) => p.split('/').slice(0, -1).join('/'),
    readFileSync: () => new Uint8Array(),
    existsSync: () => false,
    createHash: () => ({ update: () => ({ digest: () => ({ slice: () => '' }) }) }),
    createPublicKey: () => ({}),
    createPrivateKey: () => ({}),
    verify: () => false,
    sign: () => '',
    generateKeyPairSync: () => ({ publicKey: {}, privateKey: {} })
};
export const join = (...args) => args.join('/');
export const resolve = (...args) => args.join('/');
export const dirname = (p) => p.split('/').slice(0, -1).join('/');
export const readFileSync = () => new Uint8Array();
export const existsSync = () => false;
export const createHash = () => ({ update: () => ({ digest: () => ({ slice: () => '' }) }) });
export const createPublicKey = () => ({});
export const createPrivateKey = () => ({});
export const verify = () => false;
export const sign = () => '';
export const generateKeyPairSync = () => ({ publicKey: {}, privateKey: {} });
