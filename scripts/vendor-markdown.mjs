import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  readFile,
  writeFile,
} from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const vendorRoot = resolve(
  repositoryRoot,
  "shared/static/markdown/vendor",
);
const checkOnly = process.argv.includes("--check");

const artifacts = [
  {
    packageName: "marked",
    packageFile: "node_modules/marked/package.json",
    source: "node_modules/marked/lib/marked.esm.js",
    target: "marked.esm.js",
    licenseSource: "node_modules/marked/LICENSE",
    licenseTarget: "licenses/marked-LICENSE",
  },
  {
    packageName: "dompurify",
    packageFile: "node_modules/dompurify/package.json",
    source: "node_modules/dompurify/dist/purify.es.mjs",
    target: "purify.es.mjs",
    licenseSource: "node_modules/dompurify/LICENSE",
    licenseTarget: "licenses/dompurify-LICENSE",
  },
];

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function readRepositoryFile(path) {
  return readFile(resolve(repositoryRoot, path));
}

async function buildManifest() {
  const lockfile = await readRepositoryFile("package-lock.json");
  const packages = [];
  for (const artifact of artifacts) {
    const packageDocument = JSON.parse(
      await readFile(resolve(repositoryRoot, artifact.packageFile), "utf8"),
    );
    const source = await readRepositoryFile(artifact.source);
    const license = await readRepositoryFile(artifact.licenseSource);
    packages.push({
      name: artifact.packageName,
      version: packageDocument.version,
      source: artifact.source,
      vendored_file: artifact.target,
      sha256: sha256(source),
      license_file: artifact.licenseTarget,
      license_sha256: sha256(license),
    });
  }
  return {
    schema_version: 1,
    package_lock_sha256: sha256(lockfile),
    packages,
  };
}

function serializeManifest(manifest) {
  return `${JSON.stringify(manifest, null, 2)}\n`;
}

async function vendor() {
  await mkdir(resolve(vendorRoot, "licenses"), { recursive: true });
  for (const artifact of artifacts) {
    await copyFile(
      resolve(repositoryRoot, artifact.source),
      resolve(vendorRoot, artifact.target),
    );
    await copyFile(
      resolve(repositoryRoot, artifact.licenseSource),
      resolve(vendorRoot, artifact.licenseTarget),
    );
  }
  const manifest = await buildManifest();
  await writeFile(
    resolve(vendorRoot, "manifest.json"),
    serializeManifest(manifest),
    "utf8",
  );
}

async function verify() {
  const expectedManifest = await buildManifest();
  const manifestPath = resolve(vendorRoot, "manifest.json");
  const actualManifest = await readFile(manifestPath, "utf8");
  if (actualManifest !== serializeManifest(expectedManifest)) {
    throw new Error("Markdown vendor manifest does not match locked packages");
  }
  for (const artifact of artifacts) {
    const source = await readRepositoryFile(artifact.source);
    const vendored = await readFile(resolve(vendorRoot, artifact.target));
    if (sha256(source) !== sha256(vendored)) {
      throw new Error(`${artifact.target} differs from its locked package`);
    }
    const sourceLicense = await readRepositoryFile(artifact.licenseSource);
    const vendoredLicense = await readFile(
      resolve(vendorRoot, artifact.licenseTarget),
    );
    if (sha256(sourceLicense) !== sha256(vendoredLicense)) {
      throw new Error(
        `${artifact.licenseTarget} differs from its locked package`,
      );
    }
  }
}

await (checkOnly ? verify() : vendor());
