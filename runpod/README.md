# Running Comic Translate on RunPod

This app is a Qt desktop program. There is no web build of it, and rewriting
the editor canvas for a browser would mean rewriting the ~30,000 lines under
`app/ui`. So instead of porting it, this runs the real app on a GPU pod against
a virtual X display and hands that display to your browser over RunPod's HTTPS
proxy. You get the actual application — every OCR engine, the editor canvas,
PSD export — from any machine with a browser, on a GPU you rent by the hour.

The trade-off is honest: it is a remote desktop, not a web app. It is good on a
laptop or tablet, and cramped on a phone, because the editor canvas wants room.

## Build and push

From the repository root (the build context is the root, not `runpod/`):

```bash
docker build -f runpod/Dockerfile -t <dockerhub-user>/comic-translate:runpod .
docker push <dockerhub-user>/comic-translate:runpod
```

Roughly 4 GB. To include the PaddleOCR-VL engine, which needs the whole PyTorch
stack and takes the image past 10 GB:

```bash
docker build -f runpod/Dockerfile --build-arg WITH_PADDLE_VL=1 \
  -t <dockerhub-user>/comic-translate:runpod-vl .
```

The other ten OCR engines run on onnxruntime alone and are in the base image.

## Pod template

| Setting | Value |
|---|---|
| Container image | `<dockerhub-user>/comic-translate:runpod` |
| Container disk | 20 GB (30 GB for the `-vl` image) |
| Volume disk | 50 GB or more |
| Volume mount path | `/workspace` |
| Expose HTTP ports | `6080` |
| Expose TCP ports | *(none)* |

Environment variables:

| Name | Default | What it does |
|---|---|---|
| `VNC_PASSWORD` | *generated each boot* | Password for the display. **Set this.** |
| `RESOLUTION` | `1920x1080` | Virtual screen size. Match your own screen. |
| `DATA_ROOT` | `/workspace/comic-translate` | Where everything persistent lives. |

If you leave `VNC_PASSWORD` unset, a random one is generated and printed to the
pod log on every boot — usable, but it changes each restart.

A pod without a GPU works but is not worth running: LaMa inpainting and
PaddleOCR-VL are the slow steps, and PaddleOCR-VL takes about 20 seconds per
text block on a weak CPU.

## Connecting

Open `https://<pod-id>-6080.proxy.runpod.net`. It goes straight to the desktop
and asks for the password; there is no connection form to fill in.

**Turn the GPU on.** `Settings > Tools > Use GPU` is off by default, and the
checkbox is hidden entirely when no accelerator is detected — so if you cannot
see it, the pod has no usable GPU and something is wrong with the template.

To paste an API key, use noVNC's clipboard panel on the left-hand toolbar
rather than your browser's paste shortcut.

## Getting comics in and out

Files have to be inside the pod for the app's file dialogs to see them. Drag and
drop through the browser does not cross into the remote display.

Install [`runpodctl`](https://github.com/runpod/runpodctl) locally, then from
the pod's web terminal:

```bash
# receiving into the pod
runpodctl receive <code>

# sending finished pages back out
runpodctl send /workspace/comic-translate/comics/chapter-01
```

`$DATA_ROOT/comics` exists for this. If you work on one series regularly, put it
on a RunPod Network Volume instead and attach that volume to the pod, so the
files are there the moment it boots.

## What survives a restart

Everything under `$DATA_ROOT`, which is on the volume:

| Path | Contents |
|---|---|
| `data/ComicTranslate/models/` | every downloaded model weight |
| `config/ComicLabs/ComicTranslate.conf` | settings and API keys |
| `data/ComicTranslate/` | glossaries, prompt presets, workspaces, fonts |
| `huggingface/` | PaddleOCR-VL checkpoints |
| `home/Documents/` | project autosaves |
| `comics/` | your pages |

Models download lazily on first use, so the first detect/OCR/clean of a fresh
volume is slow and every run after that is not. Nothing is written to the
container filesystem, so stopping and starting the pod loses nothing.

## Notes

- Keyring is deliberately set to the failing backend. A container has no D-Bus
  secret service, and that is what makes `app/account/auth/token_storage.py`
  fall back to QSettings on the volume. A silent no-op backend would accept the
  account token and quietly drop it.
- The app is supervised by a loop in `start.sh`: if it crashes, or you close the
  window, it comes back in three seconds. Closing the window is not how you stop
  the pod — stop the pod.
- The screen size is fixed at `RESOLUTION` for the life of the pod. noVNC
  scales it to your browser window, so nothing is ever cut off, but it is only
  pixel-for-pixel sharp when `RESOLUTION` matches your own screen — set it to
  `2560x1440` or `3840x2160` if that is what you are working on. Remote
  resizing is not available: it needs the server to negotiate a new mode, and
  Xvfb only advertises the one it started with.

---

# The cloud cleaner (serverless)

Separate from everything above. The pod described so far runs the *whole app* on
a GPU you rent by the hour. This runs only the **cleaning step** on a GPU you
rent by the **second**, and the app stays on your own machine.

That difference is the point: a serverless endpoint costs nothing while idle, so
you pay for the seconds spent inpainting and nothing else. Cleaning is the
slowest local step — it is why the pod above exists — and it is the only step
worth renting a GPU for.

## Build and deploy

```bash
docker build -f runpod/Dockerfile.serverless -t <dockerhub-user>/comic-translate:cleaner .
docker push <dockerhub-user>/comic-translate:cleaner
```

Then create a RunPod **Serverless** endpoint (not a Pod) from that image.

| Setting | Value |
|---|---|
| Container image | `<dockerhub-user>/comic-translate:cleaner` |
| Container disk | 15 GB |
| Network volume | recommended — see below |

Attach a network volume mounted at `/runpod-volume`. The LaMa weights download
on the first job, and a worker that scales to zero re-downloads them on every
cold start without one. `XDG_DATA_HOME` already points there.

## Point the app at it

`Settings > Credentials > Cloud Cleaner` takes the endpoint URL and your RunPod
API key, then pick **Cloud Cleaner** in `Settings > Tools > Inpainter`.

Paste whichever URL the RunPod console shows you — a trailing `/runsync` or
`/run` is stripped, because appending another one produces a 404 that reads
like a broken deployment.

## What it costs, and what it sends

RunPod bills queue time plus execution **per job**, and each job pays its own
cold start. The client sends **one request per whole page** rather than one per
text region for exactly that reason: a typical page merges into around eight
regions, which would be eight billed jobs for the same pixels.

**Your pages leave your machine.** The page and its mask are uploaded to the
endpoint to be cleaned. The endpoint is yours and the bill is yours — there is
no service of ours in between — but for unpublished work that is still a choice
worth making deliberately.

## Trying it without Docker or a GPU

`runpod/local_server.py` runs the same handler on your own machine behind the
same HTTP contract, so the whole path can be exercised before any of the above:

```bash
python runpod/local_server.py --port 8000 --api-key anything
```

Then put `http://127.0.0.1:8000` and that key into
`Settings > Credentials > Cloud Cleaner`. LaMa runs locally, so it is CPU-slow —
the point is that every byte crosses a socket exactly as it would in production.
It is also the fastest way to tell a handler problem from a deployment problem
when a real endpoint misbehaves.

Development tool only: no TLS, binds to localhost.

## The contract

`modules/inpainting/remote.py` is the only client, and
`runpod/serverless_handler.py` the only server:

```
input:  {"image": <base64 JPEG>, "mask": <base64 PNG>, "hd_strategy": str}
output: {"image": <base64 PNG>}   or   {"error": str}
```

The mask is PNG and the reply is PNG on purpose. A JPEG mask picks up ringing at
every hard edge, which the server would read as "partly clean this" and smear
the boundary of every region; a JPEG reply would put a lossy generation in the
middle of a page that gets re-encoded again on export.

The handler imports the app's own `modules/inpainting/lama.py` rather than
reimplementing it, so the two halves cannot drift on padding, mask polarity or
channel order. `tests/test_cloud_cleaner_contract.py` runs both against each
other with the model faked.
