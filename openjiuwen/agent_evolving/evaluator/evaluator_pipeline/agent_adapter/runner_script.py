def get_runner_script() -> str:
    return '''
import sys
import os
import json
import traceback
import subprocess
import time
import uuid
import urllib.request
import urllib.error
import asyncio

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ.setdefault("EVOLUTION_AUTO_SCAN", "true")
os.environ.setdefault("EVOLUTION_AUTO_SAVE", "true")

_ACP_STDOUT = open(sys.stdout.fileno(), "w", closefd=False)

_AGENT_TIMEOUT = 800

def _error_result(err_msg):
    return {"final_response": "", "messages": [], "failed": True, "partial": False, "error": err_msg}

_EVOLUTION_WAIT_SECONDS = int(os.environ.get("JIUWENSWARM_EVOLUTION_WAIT", "60"))

def _wait_for_ws_port(host, port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            conn = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2)
            conn.close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.5)
    return False

async def _run_agent_async():
    agent_proc = None
    gateway_proc = None
    final_response = ""
    try:
        agent_log = open("/tmp/jiuwenswarm_agent_server.log", "w")
        agent_proc = subprocess.Popen(
            [sys.executable, "-m", "jiuwenswarm.server.app_agentserver"],
            stdin=subprocess.DEVNULL,
            stdout=agent_log,
            stderr=subprocess.STDOUT,
        )
        agent_host = os.environ.get("AGENT_SERVER_HOST", "127.0.0.1")
        agent_port = int(os.environ.get("AGENT_SERVER_PORT", "18092"))
        if not _wait_for_ws_port(agent_host, agent_port, timeout=60):
            agent_log.close()
            err_detail = ""
            try:
                with open("/tmp/jiuwenswarm_agent_server.log", "r", errors="replace") as f:
                    err_detail = f.read()
            except Exception:
                pass
            return _error_result(f"AgentServer failed to start on port {agent_port}: {err_detail}")

        gateway_log = open("/tmp/jiuwenswarm_gateway.log", "w")
        gateway_proc = subprocess.Popen(
            [sys.executable, "-m", "jiuwenswarm.gateway.app_gateway"],
            stdin=subprocess.DEVNULL,
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
        )
        gateway_host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
        gateway_port = int(os.environ.get("GATEWAY_PORT", "19000"))
        if not _wait_for_ws_port(gateway_host, gateway_port, timeout=60):
            gateway_log.close()
            err_detail = ""
            try:
                with open("/tmp/jiuwenswarm_gateway.log", "r", errors="replace") as f:
                    err_detail = f.read()
            except Exception:
                pass
            return _error_result(f"Gateway failed to start on port {gateway_port}: {err_detail}")

        with open("/tmp/jiuwenswarm_instruction.txt", "r", encoding="utf-8") as f:
            instruction = f.read().strip()

        if not instruction:
            return _error_result("Empty instruction")

        system_message = ""
        try:
            with open("/tmp/jiuwenswarm_system_message.txt", "r", encoding="utf-8") as f:
                system_message = f.read().strip()
        except FileNotFoundError:
            pass

        full_instruction = instruction
        if system_message:
            full_instruction = system_message + "\\n\\n---\\n\\n" + instruction

        try:
            from websockets.legacy.client import connect as ws_connect
        except ImportError:
            from websockets import connect as ws_connect

        ws_url = f"ws://{gateway_host}:{gateway_port}/ws"
        sys.stderr.write(f"[RUNNER] Connecting to WebChannel: {ws_url}\\n")
        sys.stderr.flush()

        async with ws_connect(ws_url, max_size=8 * 2**20) as ws:
            session_id = f"harbor_{uuid.uuid4().hex[:8]}"

            init_req_id = f"init_{uuid.uuid4().hex[:8]}"
            init_frame = {
                "type": "req",
                "id": init_req_id,
                "method": "initialize",
                "params": {"session_id": session_id},
            }
            await ws.send(json.dumps(init_frame, ensure_ascii=False))
            sys.stderr.write(f"[RUNNER] Sent initialize, session_id={session_id}\\n")
            sys.stderr.flush()

            init_resp = None
            t0 = time.time()
            while (time.time() - t0) < 30:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw)
                sys.stderr.write(f"[RUNNER RECV] {raw[:500]}\\n")
                sys.stderr.flush()
                if data.get("type") == "res" and data.get("id") == init_req_id:
                    init_resp = data
                    break
                if data.get("type") == "event" and data.get("event") == "connection.ack":
                    break
            if init_resp is None and not any(True for _ in []):
                pass

            session_req_id = f"session_{uuid.uuid4().hex[:8]}"
            session_frame = {
                "type": "req",
                "id": session_req_id,
                "method": "session.create",
                "params": {"session_id": session_id},
            }
            await ws.send(json.dumps(session_frame, ensure_ascii=False))
            sys.stderr.write(f"[RUNNER] Sent session.create\\n")
            sys.stderr.flush()

            session_resp = None
            t0 = time.time()
            while (time.time() - t0) < 30:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(raw)
                if data.get("type") == "res" and data.get("id") == session_req_id:
                    session_resp = data
                    break
            if session_resp and not session_resp.get("ok"):
                err = session_resp.get("error", "unknown")
                return _error_result(f"session.create failed: {err}")

            chat_req_id = f"chat_{uuid.uuid4().hex[:8]}"
            chat_frame = {
                "type": "req",
                "id": chat_req_id,
                "method": "chat.send",
                "params": {
                    "session_id": session_id,
                    "content": full_instruction,
                    "mode": "agent.plan",
                },
            }
            await ws.send(json.dumps(chat_frame, ensure_ascii=False))
            sys.stderr.write(f"[RUNNER] Sent chat.send, content_len={len(full_instruction)}\\n")
            sys.stderr.flush()

            final_response = ""
            done = False
            t0 = time.time()
            last_log_time = t0

            messages = []
            current_assistant_msg = {"role": "assistant", "content": ""}
            current_tool_calls = []
            tool_results_buffer = {}
            tool_id_to_position = {}
            evolution_events = []

            def _flush_current_round():
                nonlocal current_assistant_msg, current_tool_calls
                if current_assistant_msg.get("content") or current_tool_calls:
                    if current_tool_calls:
                        current_assistant_msg["tool_calls"] = current_tool_calls.copy()
                    messages.append(current_assistant_msg.copy())

                    for tool_call in current_tool_calls:
                        tool_id = tool_call.get("id", "")
                        tool_result = tool_results_buffer.get(tool_id, "")
                        if not tool_result:
                            position_key = tool_id_to_position.get(tool_id)
                            if position_key and position_key in tool_results_buffer:
                                tool_result = tool_results_buffer[position_key]
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result
                        }
                        messages.append(tool_msg)

                current_assistant_msg = {"role": "assistant", "content": ""}
                current_tool_calls = []

            iteration_count = 0
            evolution_wait_start = None
            while (time.time() - t0) < _AGENT_TIMEOUT:
                iteration_count += 1

                if done and evolution_wait_start is None:
                    evolution_wait_start = time.time()
                    sys.stderr.write("[RUNNER] chat.final received, now waiting for evolution events...\\n")
                    sys.stderr.flush()

                if evolution_wait_start is not None:
                    evolution_elapsed = time.time() - evolution_wait_start
                    if evolution_elapsed >= _EVOLUTION_WAIT_SECONDS:
                        sys.stderr.write(f"[RUNNER] Evolution wait timeout ({_EVOLUTION_WAIT_SECONDS}s), stopping event loop\\n")
                        sys.stderr.flush()
                        break

                current_time = time.time()
                if current_time - last_log_time >= 10:
                    elapsed = current_time - t0
                    evo_info = f", evolution_wait={evolution_elapsed:.1f}s" if evolution_wait_start else ""
                    sys.stderr.write(f"[RUNNER] Still waiting... elapsed={elapsed:.1f}s, iterations={iteration_count}, response_len={len(final_response)}{evo_info}\\n")
                    sys.stderr.flush()
                    last_log_time = current_time

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    sys.stderr.write(f"[RUNNER] WebSocket recv error: {e}\\n")
                    sys.stderr.flush()
                    break

                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    sys.stderr.write(f"[RUNNER] JSON decode error: {raw[:200]}\\n")
                    sys.stderr.flush()
                    continue

                frame_type = data.get("type")

                if frame_type == "res":
                    req_id = data.get("id")
                    if req_id == chat_req_id:
                        if not data.get("ok"):
                            err = data.get("error", "unknown")
                            return _error_result(f"chat.send failed: {err}")
                    continue

                if frame_type == "event":
                    event_name = data.get("event", "")
                    payload = data.get("payload", {})

                    if event_name == "chat.delta":
                        if current_tool_calls and current_assistant_msg.get("content"):
                            _flush_current_round()
                        content = payload.get("content", "")
                        if content:
                            final_response += content
                            current_assistant_msg["content"] += content

                    elif event_name == "chat.tool_call":
                        tool_call_info = payload.get("tool_call", {})
                        tool_id = tool_call_info.get("tool_call_id", tool_call_info.get("id", ""))
                        if not tool_id:
                            tool_id = f"tool_{len(current_tool_calls)}"
                        tool_name = tool_call_info.get("name", "unknown")
                        tool_args = tool_call_info.get("arguments", {})

                        tool_call_entry = {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args) if isinstance(tool_args, dict) else str(tool_args)
                            }
                        }
                        current_tool_calls.append(tool_call_entry)
                        position_key = f"pos_{len(current_tool_calls) - 1}"
                        tool_id_to_position[tool_id] = position_key

                    elif event_name == "chat.tool_result":
                        tool_id = payload.get("tool_call_id", payload.get("id", ""))
                        tool_result = payload.get("result", payload.get("output", ""))
                        raw_output = payload.get("raw_output")
                        if isinstance(tool_result, (dict, list)):
                            try:
                                tool_result = json.dumps(tool_result, ensure_ascii=False)
                            except (TypeError, ValueError):
                                tool_result = str(tool_result)
                        elif tool_result is not None:
                            tool_result = str(tool_result)
                        else:
                            tool_result = ""
                        if raw_output is not None:
                            if isinstance(raw_output, (dict, list)):
                                try:
                                    tool_result = json.dumps(raw_output, ensure_ascii=False)
                                except (TypeError, ValueError):
                                    tool_result = str(raw_output)
                            elif isinstance(raw_output, str):
                                tool_result = raw_output
                        if tool_id:
                            tool_results_buffer[tool_id] = tool_result
                            for tc_id, pos_key in tool_id_to_position.items():
                                if tc_id == tool_id:
                                    tool_results_buffer[pos_key] = tool_result
                                    break
                            else:
                                if current_tool_calls:
                                    last_tc = current_tool_calls[-1]
                                    last_tc_id = last_tc.get("id", "")
                                    if last_tc_id and last_tc_id not in tool_results_buffer:
                                        tool_results_buffer[last_tc_id] = tool_result
                                        tool_id_to_position[last_tc_id] = f"pos_{len(current_tool_calls) - 1}"

                    elif event_name == "chat.final":
                        content = payload.get("content", "")
                        if content and not final_response:
                            final_response = content
                        done = True

                    elif event_name == "chat.error":
                        err_content = payload.get("content", payload.get("error", "unknown error"))
                        return _error_result(f"Agent error: {err_content}")

                    elif event_name == "chat.processing_status":
                        is_processing = payload.get("is_processing", True)
                        if not is_processing and not final_response:
                            done = True

                    elif event_name == "chat.evolution_status":
                        evo_status = payload.get("status", "")
                        evo_request_id = payload.get("request_id", "")
                        evo_skill_name = payload.get("skill_name", "")
                        evo_detail = payload.get("detail", "")
                        evolution_events.append({
                            "event": "evolution_status",
                            "status": evo_status,
                            "request_id": evo_request_id,
                            "skill_name": evo_skill_name,
                            "detail": evo_detail,
                            "timestamp": time.time(),
                        })
                        if evo_status == "start":
                            sys.stderr.write(f"[RUNNER] Evolution START: skill={evo_skill_name} request_id={evo_request_id}\\n")
                            sys.stderr.flush()
                        elif evo_status == "end":
                            sys.stderr.write(f"[RUNNER] Evolution END: skill={evo_skill_name} request_id={evo_request_id}\\n")
                            sys.stderr.flush()
                        else:
                            sys.stderr.write(f"[RUNNER] Evolution status: {evo_status} skill={evo_skill_name}\\n")
                            sys.stderr.flush()

                    elif event_name == "chat.ask_user_question":
                        question_request_id = payload.get("request_id", "")
                        question_text = payload.get("question", payload.get("text", ""))
                        options = payload.get("options", [])
                        is_evolution_approval = (
                            isinstance(question_request_id, str) and
                            (question_request_id.startswith("skill_evolve_") or
                             question_request_id.startswith("team_skill_evolve_"))
                        )
                        evolution_events.append({
                            "event": "ask_user_question",
                            "request_id": question_request_id,
                            "question": question_text,
                            "is_evolution_approval": is_evolution_approval,
                            "timestamp": time.time(),
                        })
                        if is_evolution_approval:
                            sys.stderr.write(f"[RUNNER] Evolution approval request detected: request_id={question_request_id}\\n")
                            sys.stderr.flush()
                            answer_req_id = f"auto_evolve_{uuid.uuid4().hex[:8]}"
                            answer_frame = {
                                "type": "req",
                                "id": answer_req_id,
                                "method": "chat.user_answer",
                                "params": {
                                    "session_id": session_id,
                                    "request_id": question_request_id,
                                    "answers": [{"selected_options": ["接收"]}],
                                    "source": "auto_accept",
                                },
                            }
                            await ws.send(json.dumps(answer_frame, ensure_ascii=False))
                            sys.stderr.write(f"[RUNNER] Auto-approved evolution: request_id={question_request_id}\\n")
                            sys.stderr.flush()
                        else:
                            sys.stderr.write(f"[RUNNER] Non-evolution question: request_id={question_request_id} text={str(question_text)[:200]}\\n")
                            sys.stderr.flush()

            _flush_current_round()

        trajectory = [
            {"role": "user", "content": full_instruction}
        ]
        trajectory.extend(messages)

        timed_out = (time.time() - t0) >= _AGENT_TIMEOUT and not done

        agent_log_content = ""
        gateway_log_content = ""
        try:
            with open("/tmp/jiuwenswarm_agent_server.log", "r", errors="replace") as f:
                agent_log_content = f.read(20000)
        except Exception:
            pass
        try:
            with open("/tmp/jiuwenswarm_gateway.log", "r", errors="replace") as f:
                gateway_log_content = f.read(20000)
        except Exception:
            pass

        if timed_out:
            return _error_result(f"Agent execution timed out after {_AGENT_TIMEOUT}s. AgentServer log: {agent_log_content[:10000]}. Gateway log: {gateway_log_content[:10000]}")

        if not final_response:
            return _error_result(f"Agent returned empty response. AgentServer log: {agent_log_content[:10000]}. Gateway log: {gateway_log_content[:10000]}")

        return {
            "final_response": final_response,
            "messages": trajectory,
            "evolution_events": evolution_events,
            "failed": False,
            "partial": False,
        }

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return _error_result(str(e))
    finally:
        for p in [gateway_proc, agent_proc]:
            if p:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()

def _run_agent():
    return asyncio.run(_run_agent_async())

result = _run_agent()
_ACP_STDOUT.write("===JIUWENSWARM_OUTPUT_START===\\n")
_ACP_STDOUT.write(json.dumps(result, ensure_ascii=False, default=str) + "\\n")
_ACP_STDOUT.write("===JIUWENSWARM_OUTPUT_END===\\n")
_ACP_STDOUT.flush()
'''
