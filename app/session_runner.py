from __future__ import annotations
import argparse,json,mimetypes
from pathlib import Path
import httpx

def run(session_file:Path,output:Path,base_url:str):
    data=json.loads(session_file.read_text(encoding="utf-8")); output.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=base_url,timeout=120) as client:
        f=Path(data["video"])
        with f.open("rb") as stream:r=client.post("/api/v1/videos",files={"file":(f.name,stream,mimetypes.guess_type(f.name)[0] or "application/octet-stream")})
        r.raise_for_status();vid=r.json()["video_id"];r=client.post("/api/v1/sessions",json={"video_id":vid});r.raise_for_status();sid=r.json()["session_id"];results=[];pending=None
        for step in data["steps"]:
            if "transcript" in step:r=client.put(f"/api/v1/sessions/{sid}/transcript",json={"words":step["transcript"]})
            elif "say" in step:
                r=client.post(f"/api/v1/sessions/{sid}/instructions",json={"instruction":step["say"]})
                if r.is_success:
                    data_result=r.json()
                    pending={"clarification_id":data_result.get("clarification_id"),"instruction":step["say"]} if data_result.get("status")=="needs_clarification" else None
            elif "answer" in step:
                if not pending:raise ValueError("An answer step requires an immediately preceding instruction that needs clarification")
                r=client.post(f"/api/v1/sessions/{sid}/clarifications",json={"answer":step["answer"],"clarification_id":pending["clarification_id"]});pending=None
            elif "render" in step:r=client.post(f"/api/v1/sessions/{sid}/render")
            else:raise ValueError(f"Unsupported step: {step}")
            r.raise_for_status();step_result=r.json()
            if step_result.get("status")=="rejected":raise ValueError(step_result.get("summary","Edit was rejected"))
            results.append(step_result)
        timeline=client.get(f"/api/v1/timelines/{sid}").json(); (output/"results.json").write_text(json.dumps(results,indent=2));(output/"timeline.json").write_text(json.dumps(timeline,indent=2));return results
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("session");p.add_argument("--output",default="artifacts/session");p.add_argument("--url",default="http://127.0.0.1:8000");a=p.parse_args();run(Path(a.session),Path(a.output),a.url)
