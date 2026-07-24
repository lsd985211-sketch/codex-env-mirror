#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

let sharp;
try {
  sharp = require('sharp');
} catch (error) {
  console.error(JSON.stringify({
    schema: 'baoyu-slide-deck.slide-image-validation.v1',
    ok: false,
    error: `sharp is unavailable: ${error.message}`,
    next_step: 'Use the Codex desktop bundled Node runtime; do not install or rebuild managed dependencies.'
  }));
  process.exit(2);
}

function parseArgs(argv) {
  const values = { width: 1600, height: 900, minEntropy: 0.5, minBytes: 4096 };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === '--dir') values.dir = argv[++i];
    else if (key === '--expected') values.expected = Number(argv[++i]);
    else if (key === '--width') values.width = Number(argv[++i]);
    else if (key === '--height') values.height = Number(argv[++i]);
    else if (key === '--min-entropy') values.minEntropy = Number(argv[++i]);
    else if (key === '--min-bytes') values.minBytes = Number(argv[++i]);
    else if (key === '--contact-sheet') values.contactSheet = argv[++i];
    else if (key === '--receipt') values.receipt = argv[++i];
    else throw new Error(`Unknown argument: ${key}`);
  }
  if (!values.dir || !Number.isInteger(values.expected) || values.expected < 1) {
    throw new Error('Usage: verify-rendered-slides.js --dir <render-dir> --expected <count> [--contact-sheet <png>] [--receipt <json>]');
  }
  return values;
}

function slideNumber(name) {
  const match = name.match(/(\d+)/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function sha256(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

async function validate(options) {
  const directory = path.resolve(options.dir);
  const files = fs.readdirSync(directory)
    .filter((name) => /\.(png|jpg|jpeg)$/i.test(name) && /\d/.test(name))
    .sort((left, right) => slideNumber(left) - slideNumber(right));
  const errors = [];
  const pages = [];

  if (files.length !== options.expected) {
    errors.push(`image count mismatch: expected ${options.expected}, got ${files.length}`);
  }

  const numbers = files.map(slideNumber);
  const expectedNumbers = Array.from({ length: options.expected }, (_, index) => index + 1);
  if (numbers.length === options.expected && numbers.some((value, index) => value !== expectedNumbers[index])) {
    errors.push(`slide numbering is not contiguous: ${numbers.join(',')}`);
  }

  const thumbnails = [];
  const thumbWidth = 400;
  const thumbHeight = Math.round(thumbWidth * options.height / options.width);
  for (let index = 0; index < files.length; index += 1) {
    const name = files[index];
    const filePath = path.join(directory, name);
    const stat = fs.statSync(filePath);
    const metadata = await sharp(filePath).metadata();
    const stats = await sharp(filePath).stats();
    const pageErrors = [];
    if (metadata.width !== options.width || metadata.height !== options.height) {
      pageErrors.push(`dimensions ${metadata.width}x${metadata.height}`);
    }
    if (stat.size < options.minBytes) pageErrors.push(`file size ${stat.size} bytes`);
    if (stats.entropy < options.minEntropy) pageErrors.push(`entropy ${stats.entropy.toFixed(3)}`);
    if (pageErrors.length) errors.push(`${name}: ${pageErrors.join(', ')}`);
    pages.push({
      index: index + 1,
      file: name,
      width: metadata.width,
      height: metadata.height,
      bytes: stat.size,
      entropy: Number(stats.entropy.toFixed(3)),
      sha256: sha256(filePath),
      ok: pageErrors.length === 0
    });
    thumbnails.push(await sharp(filePath).resize(thumbWidth, thumbHeight).png().toBuffer());
  }

  const contactSheet = path.resolve(options.contactSheet || path.join(directory, 'contact-sheet.png'));
  if (thumbnails.length) {
    const columns = Math.min(4, thumbnails.length);
    const rows = Math.ceil(thumbnails.length / columns);
    await sharp({
      create: { width: columns * thumbWidth, height: rows * thumbHeight, channels: 4, background: '#ffffff' }
    }).composite(thumbnails.map((input, index) => ({
      input,
      left: (index % columns) * thumbWidth,
      top: Math.floor(index / columns) * thumbHeight
    }))).png().toFile(contactSheet);
  }

  const result = {
    schema: 'baoyu-slide-deck.slide-image-validation.v1',
    ok: errors.length === 0,
    render_directory: directory,
    expected_slides: options.expected,
    validated_slides: files.length,
    expected_dimensions: `${options.width}x${options.height}`,
    minimum_entropy: options.minEntropy,
    contact_sheet: contactSheet,
    errors,
    pages
  };
  const receipt = path.resolve(options.receipt || path.join(directory, 'validation-slide-images.json'));
  fs.writeFileSync(receipt, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({ ...result, pages: undefined, receipt }));
  return result;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
    const result = await validate(options);
    if (!result.ok) process.exitCode = 1;
  } catch (error) {
    console.error(JSON.stringify({ schema: 'baoyu-slide-deck.slide-image-validation.v1', ok: false, error: error.message }));
    process.exitCode = 1;
  }
}

main();
