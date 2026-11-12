const { readFileSync } = require("node:fs");
const { join } = require("node:path");

function loadEngine(directory) {
    const runtime = join(directory, "bergamot-translator.js");
    const factory = new Function(
        "require", "__dirname", "__filename",
        readFileSync(runtime, "utf8") + "\nreturn loadBergamot;"
    )(require, directory, runtime);

    return new Promise((resolve, reject) => {
        const engine = factory({
            INITIAL_MEMORY: 234_291_200,
            wasmBinary: readFileSync(join(directory, "bergamot-translator.wasm")),
            print: () => {},
            printErr: () => {},
            onAbort: () => reject(new Error("Bergamot initialization failed")),
            onRuntimeInitialized: () => queueMicrotask(() => resolve(engine)),
        });
    });
}

function aligned(engine, path, alignment) {
    const bytes = readFileSync(path);
    const memory = new engine.AlignedMemory(bytes.length, alignment);
    memory.getByteArrayView().set(bytes);
    return memory;
}

function translate(engine, service, directory, from, to, text) {
    const pair = from + to;
    const weights = aligned(engine, join(directory, "model." + pair + ".intgemm.alphas.bin"), 256);
    const shortlist = aligned(engine, join(directory, "lex.50.50." + pair + ".s2t.bin"), 64);
    const vocab = aligned(engine, join(directory, "vocab." + pair + ".spm"), 64);
    const vocabs = new engine.AlignedMemoryList();
    vocabs.push_back(vocab);
    const config = [
        "beam-size: 1",
        "normalize: 1.0",
        "max-length-break: 128",
        "mini-batch-words: 1024",
        "workspace: 128",
        "max-length-factor: 2.0",
        "skip-cost: true",
        "cpu-threads: 0",
        "quiet: true",
        "quiet-translation: true",
        "gemm-precision: int8shiftAlphaAll",
        "alignment: soft",
    ].join("\n");

    const model = new engine.TranslationModel(from, to, config, weights, shortlist, vocabs, null);
    const input = new engine.VectorString();
    const options = new engine.VectorResponseOptions();
    input.push_back(text);
    options.push_back({ qualityScores: false, alignment: false, html: false });
    let output;
    try {
        output = service.translate(model, input, options);
        return output.get(0).getTranslatedText();
    } finally {
        output?.delete();
        options.delete();
        input.delete();
        model.delete();
        vocabs.delete();
        vocab.delete();
        shortlist.delete();
        weights.delete();
    }
}

async function main() {
    const directory = process.argv[2];
    const input = JSON.parse(readFileSync(0, "utf8"));
    const engine = await loadEngine(directory);
    const service = new engine.BlockingService({ cacheSize: 0 });
    try {
        // Give the general-purpose model context for short biomedical phrases.
        const query = input.text.trim().replace(/[.!?]+$/, "");
        const forward = translate(engine, service, directory, "ru", "en", "Исследование: " + query + ".");
        const translated = forward.replace(/^\s*(?:research(?: topic)?|study(?: topic)?)\s*:\s*/i, "");
        const backward = translate(engine, service, directory, "en", "ru", "Study: " + translated);
        const backTranslation = backward.replace(/^\s*(?:исследование|тема исследования)\s*:\s*/iu, "");
        process.stdout.write(JSON.stringify({ translated, backTranslation }));
    } finally {
        service.delete();
    }
}

main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
