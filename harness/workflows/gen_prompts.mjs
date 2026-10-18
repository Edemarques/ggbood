// Render the workflow's prompts into plain Markdown files so that any AI agent can follow them.
import fs from 'node:fs'
import path from 'node:path'
// usage: node gen_prompts.mjs SCRIPT PLAN OUT_DIR [REPO_PATH] [RIDGES_BENCH_PATH] [JOBS]
const [scriptPath, planPath, outDir, repo = '/root/ggbood', samples = '/root/ridges-bench', jobs = '4'] = process.argv.slice(2)
let src = fs.readFileSync(scriptPath, 'utf8').replace(/^export const meta/m, 'const meta')
src = src.slice(0, src.indexOf('async function taskChain'))   // keep constants + prompt builders only
const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'))
const mk = new Function('args', src + '\nreturn {frameworkPrompt, buildPrompt, probePrompt, hardenPrompt, reviewPrompt, fixPrompt}')
const P = mk({ repo, samples, jobs: Number(jobs) })
fs.mkdirSync(outDir, { recursive: true })
let n = 0
for (const L of plan.langs) {
  L.tasks.forEach((spec, i) => {
    const dir = path.join(outDir, String(++n).padStart(2, '0') + '_' + spec.name)
    fs.mkdirSync(dir, { recursive: true })
    const first = L.framework && i === 0
    fs.writeFileSync(path.join(dir, '1_build.md'), first ? P.frameworkPrompt(L.lang, spec) : P.buildPrompt(spec))
    const b = { task: spec.name, language: spec.lang }
    fs.writeFileSync(path.join(dir, '2_probe.md'), P.probePrompt(b))
    fs.writeFileSync(path.join(dir, '3_harden_if_probe_solved_it.md'),
      P.hardenPrompt(b, '<paste the probe result JSON here>'))
    fs.writeFileSync(path.join(dir, '4_review.md'), P.reviewPrompt(b, '<paste the probe result JSON here>', null)
      .replace('Builder\'s report: {"task":', 'Builder\'s report (paste it here): {"task":'))
    fs.writeFileSync(path.join(dir, '5_fix_if_review_found_blocking_issues.md'),
      P.fixPrompt(b, '<paste the review result JSON here>'))
  })
}
console.log(`wrote prompts for ${n} tasks to ${outDir}`)
