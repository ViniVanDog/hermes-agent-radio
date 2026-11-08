#!/usr/bin/env python3
"""Small local dashboard for Hermes radio STT/TTS and RF events."""

import argparse

from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from baofeng_bridge.paths import PROJECT_ROOT


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes Radio</title><style>
:root { color-scheme: dark; --bg:#10151d; --panel:#18212d; --line:#2d3a49; --text:#eef4fa; --muted:#a9b8c8; --rx:#42c98b; --tx:#f4a340; }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text); font:16px system-ui,sans-serif; }
main { max-width:1080px; margin:auto; padding:24px; } header { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:18px; }
h1 { font-size:24px; margin:0 } .header-actions { display:flex; align-items:center; gap:12px; } #connection { color:var(--muted); font-size:14px } button { background:transparent; border:1px solid #c35e5e; border-radius:8px; color:#ffb4b4; cursor:pointer; font:inherit; font-size:13px; padding:7px 10px; } button:hover { background:#402126 } button:disabled { cursor:wait; opacity:.6 } .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; min-height:142px; } .label { color:var(--muted); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
.text { font-size:20px; line-height:1.38; margin-top:10px; white-space:pre-wrap; } .state { display:flex; gap:10px; margin-top:12px; flex-wrap:wrap; }
.pill { border:1px solid var(--line); border-radius:999px; padding:5px 10px; color:var(--muted); font-size:13px; } .pill.rx { color:var(--rx) } .pill.tx { color:var(--tx) }
#events { margin-top:14px; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; } .event { padding:10px 14px; border-top:1px solid var(--line); display:grid; grid-template-columns:72px 84px 1fr; gap:10px; font-size:14px; }
#usage { margin-top:14px } #usage table { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums; } #usage th,#usage td { padding:6px 8px; text-align:right; border-top:1px solid var(--line); white-space:nowrap; } #usage th:first-child,#usage td:first-child { text-align:left } #usage th { color:var(--muted); font-weight:500; font-size:12px; border-top:0 } #usage .tasks { color:var(--muted); font-size:12px; text-align:left; border-top:0; padding-top:0; white-space:normal } .scroll { overflow-x:auto; margin-top:10px }
.event:first-child { border-top:0 } .time,.kind { color:var(--muted) } .kind { text-transform:uppercase; font-size:12px; padding-top:2px; } @media(max-width:700px) { main{padding:14px}header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.event{grid-template-columns:62px 72px 1fr} }
.tabs{display:flex;gap:8px;margin:0 0 18px}.tabs button,.scan-actions button{border-color:var(--line);color:var(--text);font-size:15px;padding:10px 16px}.tabs button[aria-selected="true"]{background:#263a50;border-color:#6c9aca}.tabs button:hover,.scan-actions button:hover{background:#263a50}[hidden]{display:none!important}.scan-layout{display:grid;grid-template-columns:1fr 1.2fr;gap:14px}.scan-layout .card{min-height:0}.frequency{font-size:42px;font-weight:650;letter-spacing:-.04em;margin:14px 0 8px;font-variant-numeric:tabular-nums}.hint{color:var(--muted);font-size:13px;line-height:1.5}.scan-form{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}.scan-form label{display:grid;gap:6px;font-size:13px;color:var(--muted)}.scan-form input,.scan-form textarea,.scan-form select{width:100%;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--text);font:inherit;padding:9px}.scan-form .wide{grid-column:1/-1}.scan-form label.check{display:flex;align-items:center;gap:8px}.check input{width:auto}.scan-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}#start-scan{background:#215a45;border-color:var(--rx)}#stop-scan{border-color:#a45e50}.scan-error{color:#ffb4b4;white-space:pre-wrap;margin-top:12px}.scan-results{margin-top:14px}.scan-results table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}.scan-results th,.scan-results td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}.scan-results th{color:var(--muted);font-size:12px}.voice{color:var(--rx)}#scan-history{font-size:13px;line-height:1.7;color:var(--muted);max-height:220px;overflow:auto;margin-top:12px}@media(max-width:700px){.scan-layout{grid-template-columns:1fr}.frequency{font-size:36px}}
.preset-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0 4px}.preset-grid button{text-align:left;border-color:var(--line);color:var(--text);padding:12px;font-size:15px;line-height:1.4}.preset-grid button:hover{background:#263a50}.preset-grid small{display:block;color:var(--muted);font-size:12px;margin-top:6px}.scan-form details{border-top:1px solid var(--line);padding-top:12px}.scan-form summary{cursor:pointer;color:var(--muted);font-size:14px}.scan-form details label{margin-top:12px}@media(max-width:700px){.preset-grid{grid-template-columns:1fr}}.audio-plot{display:flex;gap:8px;margin-top:16px}.audio-axis{display:flex;flex-direction:column;justify-content:space-between;color:var(--muted);font-size:11px;width:40px;height:200px}.audio-canvases{min-width:0;flex:1}.audio-canvases canvas{display:block;width:100%;background:var(--bg)}#scanner-timeline{border-top:1px solid var(--line)}#scanner-recordings a{color:#91c8ff}#listen-scanner{border-color:#6c9aca;color:var(--text)}#scan-form details label{display:grid;gap:6px;color:var(--muted);font-size:13px;margin-top:12px}#scan-form details textarea{width:100%;padding:9px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:7px;font:inherit}#frequency-transitions{margin-top:6px;overflow-wrap:anywhere}
/* The dashboard fills the viewport; only data panes and expanded controls scroll. */
html,body{height:100%;overflow:hidden}
main{max-width:none;width:100%;height:100dvh;padding:16px;display:flex;flex-direction:column;gap:12px;overflow:hidden}
header{margin:0;flex:none}h1{font-size:22px}.tabs{margin:0;align-items:center;flex:none}.tabs button{padding:7px 16px}
#panel-hermes,#panel-scanner{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.card{min-width:0;min-height:0;padding:12px;overflow:hidden}.label{flex:none}.hint{line-height:1.35}
.hermes-conversation,.hermes-telemetry{min-width:0;min-height:0;display:flex;flex-direction:column;gap:12px}
.hermes-conversation .grid{flex:1;min-height:0;grid-template-columns:1fr;grid-template-rows:1fr 1fr;gap:12px}
.hermes-conversation .card{display:flex;flex-direction:column}.text{overflow:auto;min-height:0;overflow-wrap:anywhere;flex:1;font-size:clamp(18px,1.6vw,24px)}
.hermes-conversation>.state{margin:0;flex:none}#usage{margin:0;flex:0 1 38%;display:flex;flex-direction:column}
.scroll{min-height:0;overflow:auto}#usage .scroll{flex:1}#usage table{font-size:12px;table-layout:fixed}#usage th{white-space:normal;overflow-wrap:anywhere;min-width:0;font-size:11px;padding:5px}#usage td{padding:5px 3px}
.event-card{display:flex;flex-direction:column;flex:1;min-height:0}#events{margin:10px -12px -12px;border:0;border-radius:0;overflow:auto;flex:1;min-height:0}.event{grid-template-columns:62px 72px minmax(0,1fr);padding:9px 12px;font-size:13px}.event .body{overflow-wrap:anywhere}
.scan-layout{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr);gap:12px;min-height:0;min-width:0}.scan-layout>article:last-child{overflow:auto}
.frequency{font-size:34px;margin:6px 0}.scan-layout p{margin:8px 0}.scan-layout .state{margin-top:8px}.scan-actions{margin-top:10px;gap:8px}.scan-actions button{font-size:13px;padding:7px 10px}.scan-error:empty{display:none}.scan-form{margin-top:10px;gap:8px}.scan-form input,.scan-form select{padding:6px}.preset-grid{gap:8px;margin:8px 0}.preset-grid button{padding:9px;font-size:14px}.preset-grid small{margin-top:4px}.scan-form details{padding-top:8px}
.scan-monitor{min-width:0;min-height:0;display:grid;grid-template-rows:minmax(0,3fr) minmax(100px,1fr);gap:12px}.scan-results{margin:0;min-height:0}.audio-card{display:flex;flex-direction:column;gap:6px}.audio-card>.scan-actions{margin:0;align-items:center}.audio-card .scan-form{margin:0;grid-template-columns:minmax(0,1fr) 100px;align-items:center}.audio-card .scan-form .wide{grid-column:auto}.audio-card #recording-status{grid-column:1/-1;font-size:11px}.audio-card .scroll{flex:0 1 72px;margin:0;min-height:30px}.audio-card .scroll table{font-size:12px}.audio-card th,.audio-card td{padding:4px}.audio-plot{flex:1;min-height:100px;margin:0}.audio-axis{height:calc(100% - 40px)}.audio-canvases{display:flex;flex-direction:column;min-height:0}#scanner-spectrogram{flex:1;min-height:0;height:0}#scanner-timeline{flex:none;height:40px}.audio-card .hint{font-size:11px}#frequency-transitions{margin:0;max-height:30px;overflow:auto}#scanner-volume{width:90px}
.scan-log-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:12px;min-height:0}.scan-log-grid .card{display:flex;flex-direction:column}.scan-log-grid .scroll{margin-top:6px;flex:1}.scan-results th,.scan-results td{padding:5px;font-size:12px}#scan-history{flex:1;min-height:0;max-height:none;margin-top:6px;font-size:12px}
@media(max-height:780px) and (min-width:701px){main{padding:10px;gap:8px}.card{padding:10px}.scan-layout,.scan-monitor,#panel-hermes,#panel-scanner{gap:8px}.scan-layout>article:first-child>p:last-child{display:none}.frequency{font-size:30px}.scan-layout .hint{font-size:12px}.scan-monitor{grid-template-rows:minmax(0,4fr) minmax(90px,1fr)}}
@media(max-width:700px){main{padding:8px;gap:8px}header{flex-direction:row;align-items:center;gap:6px}h1{font-size:18px}.header-actions{gap:6px}#connection{font-size:11px}.header-actions button{font-size:11px;padding:5px}.tabs{gap:6px}.tabs button{font-size:13px;padding:6px 10px}.tabs .pill{font-size:11px;padding:4px 6px}#panel-hermes,#panel-scanner{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) minmax(0,1fr);gap:8px}.hermes-conversation .grid{grid-template-columns:1fr 1fr;grid-template-rows:1fr;gap:8px}.hermes-conversation,.hermes-telemetry{gap:6px}.text{font-size:16px}.scan-layout{grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);grid-template-rows:1fr;gap:8px}.scan-layout>article:first-child{overflow:auto}.card{padding:9px}.frequency{font-size:24px}.preset-grid{grid-template-columns:1fr}.scan-layout .hint{font-size:11px}.scan-monitor{grid-template-rows:minmax(0,3fr) minmax(65px,1fr);gap:6px}.audio-card{gap:3px}.audio-plot{min-height:40px}.audio-card .scan-form{grid-template-columns:1fr 85px}.audio-card .scroll{flex-basis:35px;min-height:25px}.audio-card #recording-status,#frequency-transitions{display:none}.audio-card .scan-form label{font-size:11px}.audio-card .scan-form input{padding:3px}.audio-card .scan-actions button{font-size:11px;padding:4px}.scan-log-grid{gap:6px}.scan-log-grid .label{font-size:10px}.audio-card .label{font-size:10px}}
</style></head><body><main><header><h1>Hermes Radio</h1><div class="header-actions"><span id="connection">connecting…</span><button id="reset-context" type="button">Clear context</button></div></header>
<nav class="tabs" role="tablist" aria-label="Dashboard mode"><button type="button" role="tab" id="tab-hermes" aria-controls="panel-hermes" aria-selected="true">Hermes</button><button type="button" role="tab" id="tab-scanner" aria-controls="panel-scanner" aria-selected="false" tabindex="-1">Scanner</button><span class="pill" id="radio-profile">Profile: checking…</span></nav>
<div id="panel-hermes" role="tabpanel" aria-labelledby="tab-hermes">
<div class="hermes-conversation"><section class="grid"><article class="card"><div class="label">STT · received audio</div><div id="stt" class="text">Waiting for the radio…</div></article><article class="card"><div class="label">TTS · Hermes replies</div><div id="tts" class="text">Waiting for a reply…</div></article></section>
<section class="state"><span id="rx" class="pill">RX idle</span><span id="tx" class="pill">TX idle</span></section>
</div><div class="hermes-telemetry"><article id="usage" class="card"><div class="label">API usage per day · LLM, STT and TTS</div><div class="scroll"><table><thead><tr><th>Day</th><th>LLM calls</th><th>Uncached tokens</th><th>Cached tokens</th><th title="Includes reasoning tokens; the visible reply may use only part of the output.">Output</th><th title="Average uncached + cached input. Not an extra charge.">Input/call</th><th title="Transcriptions made, including radio prompts dropped before the LLM.">STT</th><th>TTS (min)</th><th>Announcements</th><th>Resets</th></tr></thead><tbody id="usage-rows"><tr><td colspan="10">loading…</td></tr></tbody></table></div></article>
<article class="card event-card"><div class="label">Live history</div><section id="events" aria-label="Live history"></section></article></div></div>
<div id="panel-scanner" role="tabpanel" aria-labelledby="tab-scanner" hidden>
<section class="scan-layout"><article class="card"><div class="label">Scanner · channel 1 · receive only</div><div id="scan-frequency" class="frequency">— MHz</div><div id="scan-phase">Connecting to the radio…</div><div id="scan-progress" class="hint"></div><div class="state"><span class="pill" id="scan-speech">Waiting to listen</span><span class="pill" id="scan-elapsed">0 s</span></div><p class="hint">Set the radio's channel knob to <strong>channel 1</strong>. Only channel 1 changes; the other channels are rewritten with their original values. When you stop, the original Hermes profile is restored and verified.</p><div class="scan-actions"><button id="stop-scan" type="button" disabled>Stop and restore Hermes</button><button id="restore-hermes" type="button" disabled>Use Hermes profile</button></div><p id="scan-error" class="scan-error" role="status"></p><p class="hint">Local probable-voice detection, with no transcription and no audio sent to Hermes. Noise can cause false positives.</p></article>
<article class="card"><div class="label">Scanner profile</div><form id="scan-form"><div class="scan-form"><div class="wide"><div class="hint">Start a preset; the scanner steps through the frequencies automatically.</div><div class="preset-grid"><button id="scan-factory" type="button" disabled>Scan Baofeng factory list<small id="factory-preset-info">All frequencies from the factory backup.</small></button><button id="scan-anatel" type="button" disabled>Scan Brazil (Anatel) general use<small id="anatel-preset-info">All 26 channels, no typing needed.</small></button></div></div><details class="wide" id="full-band-controls"><summary>Full BF-777S band · RX only</summary><div class="scan-form"><label>Start · MHz<input id="sweep-start" type="number" min="400" max="470" step="0.0125" value="400"></label><label>End · MHz<input id="sweep-end" type="number" min="400" max="470" step="0.0125" value="470"></label><label>Step · kHz<select id="sweep-step"><option value="12.5">12.5 kHz</option><option value="25">25 kHz</option></select></label></div><p class="hint" id="sweep-estimate"></p><p class="hint">Nominal 400–470 MHz band; this unit's RF limits were not measured. Every step rewrites the persistent profile. This is not a fast VFO sweep.</p><div class="scan-actions"><button id="scan-full" type="button" disabled>Sweep the 777S band</button></div></details><details class="wide"><summary>Timing and voice detection</summary><div class="scan-form"><label>Initial listen · seconds<input id="scan-listen" type="number" min="2" max="60" value="5" required></label><label>Grace after voice · seconds<input id="scan-grace" type="number" min="1" max="60" value="3" required></label><label>Limit per frequency · seconds<input id="scan-hold" type="number" min="15" max="900" value="120" required></label><label>Audio floor · RMS<input id="scan-floor" type="number" min="0" max="10000" value="250" required></label></div></details><label class="check wide"><input id="scan-repeat" type="checkbox"> Repeat the list continuously</label></div><p class="hint">The factory preset uses every frequency from the backup; Anatel uses the Brazilian general-use list. Each change rewrites the radio's persistent profile: 15 s minimum interval. With voice, it waits for silence or the per-frequency limit.</p><details><summary>Custom list (optional)</summary><label>Frequencies · MHz<textarea id="scan-frequencies" rows="3" spellcheck="false" placeholder="462.6250 462.5625 462.5750"></textarea></label><div class="scan-actions"><button id="start-scan" type="submit" disabled>Scan custom list</button></div></details></form></article></section><section class="scan-monitor">
<article class="card scan-results audio-card"><div class="label">Live audio · audio spectrogram</div><div class="scan-actions"><button id="listen-scanner" type="button" disabled>Listen and visualize</button><label class="hint">Volume <input id="scanner-volume" type="range" min="0" max="100" value="70" aria-label="Scanner volume"></label><span id="audio-stream-status" class="hint">Start the scanner to listen.</span></div><div class="audio-plot"><div class="audio-axis"><span>8 kHz</span><span>4 kHz</span><span>0</span></div><div class="audio-canvases"><canvas id="scanner-spectrogram" height="200" aria-label="Spectrogram of received audio, zero to eight kHz"></canvas><canvas id="scanner-timeline" height="54" aria-label="Timeline of frequencies and tuning gaps"></canvas></div></div><div id="timeline-position" class="hint">Last 45 seconds · the timeline follows the audio being played.</div><div id="frequency-transitions" class="hint"></div><div class="scan-form"><label class="check wide"><input id="scan-record" type="checkbox"> Record voice segments to the host disk</label><label>Recording limit · MB<input id="scan-record-limit" type="number" min="16" max="102400" value="1024"></label><div id="recording-status" class="hint">Optional capture on the host; the VM only relays the stream.</div></div><div class="scroll"><table><thead><tr><th>Time</th><th>Frequency</th><th>Duration</th><th>File on host</th></tr></thead><tbody id="scanner-recordings"><tr><td colspan="4">No recordings.</td></tr></tbody></table></div></article><div class="scan-log-grid"><article class="card scan-results"><div class="label">Activity per visit</div><div class="scroll"><table><thead><tr><th>Frequency</th><th>Voice</th><th>Voice time</th><th>Listened</th><th>Exit reason</th><th>Time</th></tr></thead><tbody id="scan-results"><tr><td colspan="6">No frequency examined yet.</td></tr></tbody></table></div></article>
<article class="card scan-results"><div class="label">Scanner history</div><div id="scan-history">Waiting…</div></article></div></section>
</div></main>
<script>
let after=0; const stt=document.querySelector('#stt'),tts=document.querySelector('#tts'),rx=document.querySelector('#rx'),tx=document.querySelector('#tx'),events=document.querySelector('#events'),connection=document.querySelector('#connection'),reset=document.querySelector('#reset-context');
function timestamp(value){return new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function text(event){return event.text || event.message || (event.rms ? 'RMS '+event.rms : '') || '—'}
function render(event){ after=Math.max(after,event.id||0); if(event.kind==='stt')stt.textContent=event.text; if(event.kind==='radio_prompt_ignored')stt.textContent='Ignored · radio prompt: '+event.text; if(event.kind==='tts')tts.textContent=event.text; if(event.kind==='rx_open'){rx.textContent='RX receiving';rx.className='pill rx'} if(event.kind==='rx_closed'){rx.textContent='RX idle';rx.className='pill'} if(event.kind==='tx_begin'){tx.textContent='TX keying';tx.className='pill tx'} if(event.kind==='tx_playback'){tx.textContent='TX speaking';tx.className='pill tx'} if(event.kind==='tx_complete'){tx.textContent='TX idle';tx.className='pill'} const row=document.createElement('div');row.className='event';row.innerHTML='<span class="time"></span><span class="kind"></span><span class="body"></span>';row.children[0].textContent=timestamp(event.ts);row.children[1].textContent=event.kind==='radio_prompt_ignored'?'ignored':event.kind.replaceAll('_',' ');row.children[2].textContent=event.kind==='radio_prompt_ignored'?'Radio prompt dropped before the LLM: '+text(event):text(event);events.prepend(row);while(events.children.length>80)events.lastChild.remove();}
async function poll(){try{const response=await fetch('/api/events?after='+after,{cache:'no-store'});const data=await response.json();data.events.forEach(render);connection.textContent='live';}catch(error){connection.textContent='reconnecting…';}setTimeout(poll,500)} poll();
function k(value){return value>=1e6?(value/1e6).toFixed(1)+'M':value>=1000?Math.round(value/1000)+'k':String(value)}
function cell(row,value){const td=document.createElement('td');td.textContent=value;row.appendChild(td);return td}
async function usage(){try{const response=await fetch('/api/usage?days=7',{cache:'no-store'});const data=await response.json();const body=document.querySelector('#usage-rows');body.replaceChildren();if(!data.days.length){const tr=document.createElement('tr');cell(tr,data.error||'no data').colSpan=10;body.appendChild(tr);}
data.days.forEach(d=>{const tr=document.createElement('tr');[d.day.slice(8)+'/'+d.day.slice(5,7),d.llm_calls,k(d.input),k(d.cached),k(d.output),k(d.tokens_per_call),d.stt,d.tts+' ('+Math.round(d.tts_minutes)+')',d.announce,d.context_reset].forEach(v=>cell(tr,v));body.appendChild(tr);const tasks=Object.entries(d.by_task).sort((a,b)=>b[1]-a[1]).map(([t,n])=>t+' '+n).join(' · ');if(tasks){const extra=document.createElement('tr');const td=cell(extra,tasks);td.colSpan=10;td.className='tasks';body.appendChild(extra);}});}catch(error){}setTimeout(usage,60000)} usage();
reset.addEventListener('click',async()=>{if(!confirm('Start a new conversation? The current context and any reply in progress will be interrupted. Radio settings and logs are kept.'))return;reset.disabled=true;reset.textContent='Clearing…';try{const response=await fetch('/api/reset-context',{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not find the running Hermes.');stt.textContent='Context cleared. Waiting for the radio…';tts.textContent='The next reply starts a new conversation.';connection.textContent='new conversation started';}catch(error){connection.textContent=error.message;}finally{reset.disabled=false;reset.textContent='Clear context';}});
const tabs=[document.querySelector('#tab-hermes'),document.querySelector('#tab-scanner')];
function showTab(index){tabs.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;document.querySelector('#'+tab.getAttribute('aria-controls')).hidden=i!==index});if(index===1)scannerPoll(true)}
tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>showTab(i));tab.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?1:1-i;showTab(next);tabs[next].focus()}})});
const scanError=document.querySelector('#scan-error'),startScan=document.querySelector('#start-scan'),stopScan=document.querySelector('#stop-scan'),restoreHermes=document.querySelector('#restore-hermes'),factoryScan=document.querySelector('#scan-factory'),anatelScan=document.querySelector('#scan-anatel'),fullScan=document.querySelector('#scan-full');let scanPending=false,scanInitialized=false,scanPolling=false,scanActionError="";
const phases={idle:'Hermes active',preparing:'Preparing the scanner profile…',tuning:'Changing the channel 1 frequency…',listening:'Listening',stopping:'Stopping…',restoring:'Restoring and verifying the Hermes profile…',recovery_required:'Restore pending · TX inhibited'};
function scannerRender(data){document.dispatchEvent(new CustomEvent('scanner-status',{detail:data}));document.querySelector('#radio-profile').textContent='Profile: '+(data.profile==='hermes'?'Hermes':'Scanner · TX inhibited');document.querySelector('#scan-frequency').textContent=data.frequency_mhz?Number(data.frequency_mhz).toFixed(4)+' MHz':'— MHz';document.querySelector('#scan-phase').textContent=phases[data.phase]||data.phase;const plan=data.scan_plan||{};document.querySelector('#scan-progress').textContent=plan.current_index?'Frequency '+plan.current_index+' of '+plan.frequency_count+' · pass '+(data.pass_number||1):'';const current=data.current||{};const speech=document.querySelector('#scan-speech');speech.textContent=current.speech_active?'Probable voice now':current.speech_detected?'Voice detected this visit':data.phase==='listening'?'Listening · no voice yet':'Waiting to listen';speech.className='pill'+(current.speech_active?' rx':'');document.querySelector('#scan-elapsed').textContent=(current.elapsed_seconds||0)+' s';scanError.textContent=scanActionError||data.error||(!data.profile_saved?'The Hermes profile has not been saved yet.':!data.vad_available?'Voice detector unavailable.':'');startScan.disabled=factoryScan.disabled=anatelScan.disabled=fullScan.disabled=scanPending||!data.available||data.running||data.profile!=='hermes';stopScan.disabled=scanPending||!data.running;restoreHermes.disabled=scanPending||data.running||data.profile==='hermes';
const presets=data.frequency_presets||[];const factory=presets.find(p=>p.id==='factory');if(factory)document.querySelector('#factory-preset-info').textContent=factory.description;const general=presets.find(p=>p.id==='anatel_general');if(general)document.querySelector('#anatel-preset-info').textContent=general.description;if(!scanInitialized){const config=data.config;let custom=config.frequency_preset==='custom'?config.frequencies_mhz.map(f=>Number(f).toFixed(4)).join(' '):'';try{custom=localStorage.getItem('baofeng.scanner.custom')||custom}catch(error){}document.querySelector('#scan-frequencies').value=custom;document.querySelector('#scan-listen').value=config.listen_seconds;document.querySelector('#scan-grace').value=config.grace_seconds;document.querySelector('#scan-hold').value=config.max_hold_seconds;document.querySelector('#scan-floor').value=config.rms_floor;document.querySelector('#scan-repeat').checked=config.repeat;document.querySelector('#sweep-start').value=config.sweep_start_mhz??400;document.querySelector('#sweep-end').value=config.sweep_end_mhz??470;document.querySelector('#sweep-step').value=String(config.sweep_step_khz??12.5);updateSweepEstimate();scanInitialized=true}
const body=document.querySelector('#scan-results');body.replaceChildren();const results=(data.results||[]).slice(-100).reverse();if(!results.length){const row=document.createElement('tr');cell(row,'No frequency examined yet.').colSpan=6;body.appendChild(row)}results.forEach(result=>{const row=document.createElement('tr');cell(row,Number(result.frequency_mhz).toFixed(4)+' MHz');const voice=cell(row,result.speech_detected?'Probable voice':'No voice');if(result.speech_detected)voice.className='voice';cell(row,result.speech_seconds.toFixed(2)+' s');cell(row,result.listened_seconds.toFixed(1)+' s');cell(row,result.reason);cell(row,timestamp(result.ts));body.appendChild(row)});const history=document.querySelector('#scan-history');history.replaceChildren();(data.history||[]).slice(-25).reverse().forEach(entry=>{const row=document.createElement('div');row.textContent=timestamp(entry.ts)+' · '+entry.message;history.appendChild(row)})}
async function scannerPoll(){if(scanPolling)return;scanPolling=true;try{const response=await fetch('/api/scanner',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'Scanner unavailable');scannerRender(data)}catch(error){scanError.textContent=error.message;document.querySelector('#radio-profile').textContent='Profile: scanner disconnected';startScan.disabled=factoryScan.disabled=anatelScan.disabled=fullScan.disabled=true;stopScan.disabled=true;restoreHermes.disabled=true}finally{scanPolling=false}}
async function scannerAction(action,body={}){scanPending=true;scanActionError='';startScan.disabled=factoryScan.disabled=anatelScan.disabled=fullScan.disabled=stopScan.disabled=restoreHermes.disabled=true;scanError.textContent='';try{const response=await fetch('/api/scanner/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'Could not control the radio');scanPending=false;scannerRender(data)}catch(error){scanActionError=error.message;scanError.textContent=error.message;scanPending=false;await scannerPoll()}}
function startPreset(preset){const fields=['scan-listen','scan-grace','scan-hold','scan-floor','scan-record-limit'];if(preset==='full_band')fields.push('sweep-start','sweep-end');if(!fields.every(id=>document.getElementById(id).reportValidity()))return;const config={frequency_preset:preset,listen_seconds:Number(document.querySelector('#scan-listen').value),grace_seconds:Number(document.querySelector('#scan-grace').value),max_hold_seconds:Number(document.querySelector('#scan-hold').value),rms_floor:Number(document.querySelector('#scan-floor').value),repeat:document.querySelector('#scan-repeat').checked,record_audio:document.querySelector('#scan-record').checked,recording_limit_mb:Number(document.querySelector('#scan-record-limit').value)};if(preset==='full_band'){config.sweep_start_mhz=Number(document.querySelector('#sweep-start').value);config.sweep_end_mhz=Number(document.querySelector('#sweep-end').value);config.sweep_step_khz=Number(document.querySelector('#sweep-step').value)}if(preset==='custom'){const value=document.querySelector('#scan-frequencies').value.trim();if(!value){scanActionError='Enter the frequencies for the custom list.';scanError.textContent=scanActionError;return}config.frequencies_mhz=value.split(/[\s,;]+/).map(Number)}scannerAction('start',config)}function updateSweepEstimate(){const start=Math.round(Number(document.querySelector('#sweep-start').value)*1e6),end=Math.round(Number(document.querySelector('#sweep-end').value)*1e6),step=Math.round(Number(document.querySelector('#sweep-step').value)*1000);const text=document.querySelector('#sweep-estimate');if(!Number.isFinite(start)||!Number.isFinite(end)||!step||end<start||(end-start)%step){text.textContent='Check the limits and their alignment with the step.';return}const count=(end-start)/step+1,seconds=count*15,hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);text.textContent=count.toLocaleString()+' frequencies per pass · at least '+hours+' h '+minutes+' min of listening alone, plus programming · '+(count+1).toLocaleString()+' profile uploads including the restore.'}['sweep-start','sweep-end','sweep-step'].forEach(id=>document.getElementById(id).addEventListener('input',updateSweepEstimate));fullScan.addEventListener('click',()=>startPreset('full_band'));factoryScan.addEventListener('click',()=>startPreset('factory'));anatelScan.addEventListener('click',()=>startPreset('anatel_general'));document.querySelector('#scan-frequencies').addEventListener('input',event=>{try{localStorage.setItem('baofeng.scanner.custom',event.target.value)}catch(error){}});document.querySelector('#scan-form').addEventListener('submit',event=>{event.preventDefault();startPreset('custom')});stopScan.addEventListener('click',()=>scannerAction('stop'));restoreHermes.addEventListener('click',()=>scannerAction('hermes'));scannerPoll();setInterval(scannerPoll,1000);
</script><script src="/scanner-audio.js"></script></body></html>"""


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.events = deque(maxlen=250)
        self.offset = 0
        self.next_id = 1
        self.lock = threading.Lock()

    def refresh(self):
        with self.lock:
            try:
                size = self.path.stat().st_size
                if size < self.offset:
                    self.offset = 0
                    self.events.clear()
                with self.path.open("r", encoding="utf-8") as source:
                    source.seek(self.offset)
                    for line in source:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        event["id"] = self.next_id
                        self.next_id += 1
                        self.events.append(event)
                    self.offset = source.tell()
            except OSError:
                pass

    def after(self, event_id):
        self.refresh()
        with self.lock:
            return [event for event in self.events if event["id"] > event_id]

    def append(self, kind, **fields):
        event = {"ts": time.time(), "kind": kind, **fields}
        try:
            with self.lock:
                with self.path.open("a", encoding="utf-8") as destination:
                    destination.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass
        return event


def load_usage_module():
    """radio_bridge.usage from this checkout, else from the installed copy."""
    for root in (PROJECT_ROOT, Path("~/.local/share/baofeng-radio").expanduser()):
        if (root / "radio_bridge" / "usage.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            try:
                from radio_bridge import usage
                return usage
            except Exception:  # noqa: BLE001 - the dashboard must keep working without it
                return None
    return None


def active_hermes_process():
    """Return a running Hermes CLI PID, never a stale terminal-session marker."""
    markers = Path("~/.hermes/terminal-sessions").expanduser()
    candidates = []
    for marker in markers.glob("tty-dev-pts-*"):
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            terminal = Path("/dev/pts") / marker.name.removeprefix("tty-dev-pts-")
            timestamp = float(metadata["ts"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if terminal.exists():
            candidates.append((timestamp, terminal, metadata.get("session_id", "")))

    for _timestamp, terminal, session_id in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
        for process in Path("/proc").glob("[0-9]*"):
            try:
                command = (process / "cmdline").read_bytes()
                stdin = os.path.realpath(process / "fd" / "0")
            except OSError:
                continue
            if stdin == str(terminal) and b"hermes_cli.main" in command and b"--cli" in command:
                return int(process.name), session_id
    raise RuntimeError("No running Hermes process was found.")


def start_fresh_hermes_session():
    """Ask the live Hermes process to reset safely on its own REPL worker."""
    process_id, session_id = active_hermes_process()
    try:
        os.kill(process_id, signal.SIGUSR1)
    except OSError as error:
        raise RuntimeError("Could not send the reset to the running Hermes.") from error
    return session_id


def scanner_request(base_url, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    request = Request(base_url.rstrip('/') + '/api/' + path, data=body,
                      headers={'Content-Type': 'application/json'},
                      method='POST' if body is not None else 'GET')
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read(262144))
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read(8192))
        except ValueError:
            return error.code, {'error': 'The controller refused the operation'}
    except (OSError, URLError, ValueError):
        return 503, {'error': 'Scanner controller unavailable; the radio state could not be confirmed.'}


def handler_for(store, scanner_url='http://127.0.0.1:8788'):
    class Handler(BaseHTTPRequestHandler):
        def proxy_audio(self, path, live=False):
            # Stream directly; do not create a temporary file on the VM.
            try:
                source = urlopen(scanner_url.rstrip('/') + path, timeout=5)
            except HTTPError as error:
                self.send_json(error.code, {'error': 'Audio unavailable; check that the scanner is running.'})
                return
            except OSError:
                self.send_json(503, {'error': 'Audio controller unavailable'})
                return
            with source:
                self.send_response(200)
                self.send_header('Content-Type', source.headers.get('Content-Type', 'application/octet-stream'))
                self.send_header('Cache-Control', 'no-store')
                if live:
                    self.send_header('Connection', 'close')
                else:
                    for name in ('Content-Length', 'Content-Disposition'):
                        if source.headers.get(name):
                            self.send_header(name, source.headers[name])
                self.end_headers()
                try:
                    self.connection.settimeout(5)
                    while chunk := source.read(656 if live else 65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except OSError:
                    pass

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == '/scanner-audio.js':
                body = Path(__file__).with_name('scanner_audio_web.js').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == '/api/scanner/audio':
                self.proxy_audio('/api/audio', live=True)
                return
            if parsed.path.startswith('/api/scanner/recordings/'):
                self.proxy_audio('/api/recordings/' + parsed.path[len('/api/scanner/recordings/'):])
                return
            if parsed.path == '/api/scanner':
                status, data = scanner_request(scanner_url, 'status')
                self.send_json(status, data)
                return
            if parsed.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                try:
                    after = max(0, int(query.get("after", ["0"])[0]))
                except ValueError:
                    after = 0
                self.send_json(HTTPStatus.OK, {"events": store.after(after)})
                return
            if parsed.path == "/api/usage":
                usage = load_usage_module()
                if usage is None:
                    self.send_json(HTTPStatus.OK, {"days": [], "error": "radio_bridge.usage is not installed"})
                    return
                try:
                    days = min(60, max(1, int(parse_qs(parsed.query).get("days", ["7"])[0])))
                    self.send_json(HTTPStatus.OK, {"days": usage.summary(days, events_path=str(store.path))})
                except Exception as error:  # noqa: BLE001 - report instead of a broken page
                    self.send_json(HTTPStatus.OK, {"days": [], "error": f"{type(error).__name__}: {error}"})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self):
            scanner_actions = {'/api/scanner/start': 'start', '/api/scanner/stop': 'stop',
                               '/api/scanner/hermes': 'hermes', '/api/scanner/recording': 'recording'}
            if self.path in scanner_actions:
                origin = self.headers.get('Origin')
                if origin and urlparse(origin).netloc != self.headers.get('Host'):
                    self.send_json(HTTPStatus.FORBIDDEN, {'error': 'Origin not allowed'})
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= size <= 8192:
                        raise ValueError('Request too large')
                    data = json.loads(self.rfile.read(size) or b'{}')
                    if not isinstance(data, dict):
                        raise ValueError('Invalid configuration')
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self.send_json(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                    return
                status, result = scanner_request(scanner_url, scanner_actions[self.path], data)
                self.send_json(status, result)
                return
            if self.path != "/api/reset-context":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                previous_session = start_fresh_hermes_session()
            except RuntimeError as error:
                self.send_json(HTTPStatus.CONFLICT, {"ok": False, "error": str(error)})
                return
            store.append("context_reset", message="Context cleared; new Hermes conversation started.")
            self.send_json(HTTPStatus.OK, {"ok": True, "previous_session": previous_session})

        def log_message(self, _format, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Serve the local Hermes radio dashboard.")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--events-file", default="~/.local/state/baofeng-radio/events.jsonl")
    parser.add_argument('--scanner-url', default=os.environ.get('BAOFENG_SCANNER_URL', 'http://127.0.0.1:8788'))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    store = EventStore(Path(args.events_file).expanduser())
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(store, args.scanner_url))
    print("Dashboard: http://{}:{}".format(args.bind, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
