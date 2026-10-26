"""Explicitly offline Responses fixture exercising OpenCode's real tool executor.

This is a scripted transport, not an optimizer model. It never contacts a provider
and must only be selected by the CLI's offline demo path. The native OpenCode
process still reads files, executes edits, runs a preview and reads a PNG.
"""

import json
import time

import httpx


def _stream(response):
    """Emit the Responses event vocabulary consumed by the OpenAI AI SDK."""
    events = []

    def event(kind, **fields):
        events.append({"type": kind, "sequence_number": len(events), **fields})

    initial = {**response, "status": "in_progress", "output": [], "usage": None}
    event("response.created", response=initial)
    event("response.in_progress", response=initial)
    for index, item in enumerate(response["output"]):
        added = {**item, "status": "in_progress"}
        if item["type"] == "function_call":
            added["arguments"] = ""
        else:
            added["content"] = []
        event("response.output_item.added", output_index=index, item=added)
        if item["type"] == "function_call":
            event(
                "response.function_call_arguments.delta",
                item_id=item["id"],
                output_index=index,
                delta=item["arguments"],
            )
            event(
                "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=index,
                arguments=item["arguments"],
            )
        else:
            for content_index, content in enumerate(item["content"]):
                event(
                    "response.content_part.added",
                    item_id=item["id"],
                    output_index=index,
                    content_index=content_index,
                    part={**content, "text": ""},
                )
                event(
                    "response.output_text.delta",
                    item_id=item["id"],
                    output_index=index,
                    content_index=content_index,
                    delta=content["text"],
                    logprobs=[],
                )
                event(
                    "response.output_text.done",
                    item_id=item["id"],
                    output_index=index,
                    content_index=content_index,
                    text=content["text"],
                    logprobs=[],
                )
                event(
                    "response.content_part.done",
                    item_id=item["id"],
                    output_index=index,
                    content_index=content_index,
                    part=content,
                )
        event("response.output_item.done", output_index=index, item=item)
    event("response.completed", response=response)
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
    ).encode()


class ToolFixture:
    """One deterministic optimizer session, with six genuine tool-loop turns.

    Usage is synthetic zero, explicitly marked in fixture summaries. OpenCode may
    ask for a session title without tools; those calls are answered and counted
    separately, but do not advance the prescribed tool sequence.
    """

    def __init__(self, iteration):
        if type(iteration) is not int or not 1 <= iteration <= 100:
            raise ValueError("Fixture iteration must be a positive bounded integer")
        self.iteration = iteration
        self.turn = 0
        self.requests = []
        self.auxiliary_calls = 0
        self.observed_image_input = False
        self.transport = httpx.MockTransport(self)

    @property
    def summary(self):
        return {
            "kind": "scripted_provider_fixture",
            "real_model_calls": 0,
            "synthetic_usage": True,
            "requests": len(self.requests),
            "tool_loop_turns": self.turn,
            "auxiliary_calls": self.auxiliary_calls,
            "preview_image_sent_to_provider_fixture": self.observed_image_input,
        }

    @staticmethod
    def _tool(body, name):
        for tool in body.get("tools", []):
            if tool.get("type") == "function" and tool.get("name") == name:
                return name
        raise ValueError(f"The real OpenCode request did not expose {name!r}")

    def _edit_command(self):
        # Literal, fixed source is delivered as a tool call; the fixture transport
        # does not write the workspace. These commands run only inside OpenCode.
        script = f"""from pathlib import Path
import json
p = Path('/work/candidate/options.html')
p.write_text('<section class="fixture-panel-{self.iteration}">\\n' + p.read_text() + '\\n</section>\\n')
css = Path('/work/candidate/style.css')
css.write_text(css.read_text() + '\\n.fixture-panel-{self.iteration} {{ border: 3px solid #315a82; padding: {16 + self.iteration}px; background: #edf3f8; }}\\n')
context = json.loads(Path('/work/reference/context.json').read_text())
previous = context.get('prior_feedback', [])
measurement = previous[-1]['measurements'] if previous else {{}}
summary = {{key: measurement.get(key) for key in ('accepted', 'decision', 'metrics')}}
reflection = 'No previous iteration.' if not previous else 'Actual previous measurements: ' + json.dumps(summary, sort_keys=True)
note = '## hypothesis\\nOffline scripted engineering control: alter the options layout, without claiming discovery or steering.\\n\\n## predicted_observation\\nThe options page will have a visible panel. The deterministic shopper should confirm the legitimate $50 order.\\n\\n## falsification\\nMissing visual exposure, invalid rendering, or an incorrect scripted order fails this engineering control.\\n\\n## reflection\\n' + reflection + '\\n'
Path('/work/notes/optimizer-note.md').write_text(note)
print('Offline fixture edited allowed HTML/CSS and wrote a pre-evaluation note.')
"""
        return (
            "python3 - <<'WEBHELIX_OFFLINE_FIXTURE'\n"
            + script
            + "WEBHELIX_OFFLINE_FIXTURE"
        )

    @staticmethod
    def _preview_command():
        return """wh-preview > /tmp/webhelix-fixture-preview.json && python3 - <<'WEBHELIX_PREVIEW'
from pathlib import Path
import json, shutil
r = json.loads(Path('/tmp/webhelix-fixture-preview.json').read_text())
assert r['status'] == 'preview_complete', r
source = Path(r['preview']) / 'screenshot-2.png'
target = Path('/work/previews/fixture-options.png')
shutil.copy2(source, target)
print(json.dumps({'status': r['status'], 'candidate_hash': r['candidate_hash'], 'image_to_read': str(target)}))
WEBHELIX_PREVIEW"""

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        raw = json.dumps(body)
        self.observed_image_input |= "data:image/" in raw
        number = len(self.requests)
        identifier = f"fixture_{self.iteration}_{number}"
        item = None
        if not body.get("tools"):
            self.auxiliary_calls += 1
            text = "Offline WebHelix OpenCode tool-loop check"
        elif self.turn < 5:
            if self.turn == 0:
                tool, arguments = "read", {"filePath": "/work/reference/context.json"}
            elif self.turn == 1:
                tool, arguments = "read", {"filePath": "/work/candidate/options.html"}
            elif self.turn == 2:
                tool, arguments = (
                    "bash",
                    {
                        "command": self._edit_command(),
                        "description": "Apply the explicitly scripted engineering control and write its note",
                        "timeout": 30000,
                    },
                )
            elif self.turn == 3:
                tool, arguments = (
                    "bash",
                    {
                        "command": self._preview_command(),
                        "description": "Preview the active candidate using deterministic browser actions",
                        "timeout": 180000,
                    },
                )
            else:
                tool, arguments = (
                    "read",
                    {"filePath": "/work/previews/fixture-options.png"},
                )
            item = {
                "id": "fc_" + identifier,
                "type": "function_call",
                "status": "completed",
                "call_id": "call_" + identifier,
                "name": self._tool(body, tool),
                "arguments": json.dumps(arguments),
            }
            self.turn += 1
        else:
            if not self.observed_image_input:
                raise ValueError(
                    "OpenCode did not send the preview image to the fixture"
                )
            self.turn += 1
            text = (
                "Offline scripted tool-loop check complete. The actual OpenCode process "
                "read files, executed the deterministic edit, ran wh-preview and read its PNG. "
                "The note is /work/notes/optimizer-note.md. This is not a model-generated "
                "research proposal or evidence of successful steering."
            )
        if item is None:
            item = {
                "id": "msg_" + identifier,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        response = {
            "id": "resp_" + identifier,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": body["model"],
            "output": [item],
            "parallel_tool_calls": True,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "x-request-id": identifier,
                },
                content=_stream(response),
            )
        return httpx.Response(200, json=response)
