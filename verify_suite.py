from build_ff_suite import verify
import json
if __name__=="__main__":
 result=verify(); print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if result["ok"] else 1)

