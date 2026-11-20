from voyager.utils import parse_llm_json
from voyager.prompts import load_prompt
from voyager.utils.json_utils import fix_and_parse_json
from voyager.intent import call_llm_with_retry
from langchain.schema import HumanMessage, SystemMessage
from voyager.llm_meta import create_chat_openai


class CriticAgent:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        request_timout=120,
        mode="auto",
        thinking="disabled",
        llm_audit=None,
    ):
        self.llm = create_chat_openai(
            model_name=model_name,
            temperature=temperature,
            request_timeout=request_timout,
            thinking=thinking,
            audit=llm_audit,
            agent="critic",
        )
        assert mode in ["auto", "manual"]
        self.mode = mode

    def render_system_message(self):
        system_message = SystemMessage(content=load_prompt("critic"))
        return system_message

    def render_human_message(
        self, *, events, task, context, chest_observation, executed_skills=None
    ):
        assert events[-1][0] == "observe", "Last event must be observe"
        biome = events[-1][1]["status"]["biome"]
        time_of_day = events[-1][1]["status"]["timeOfDay"]
        voxels = events[-1][1]["voxels"]
        health = events[-1][1]["status"]["health"]
        hunger = events[-1][1]["status"]["food"]
        position = events[-1][1]["status"]["position"]
        equipment = events[-1][1]["status"]["equipment"]
        inventory_used = events[-1][1]["status"]["inventoryUsed"]
        inventory = events[-1][1]["inventory"]

        for i, (event_type, event) in enumerate(events):
            if event_type == "onError":
                print(f"\033[31mCritic Agent: Error occurs {event['onError']}\033[0m")
                return None

        observation = ""

        observation += f"Biome: {biome}\n\n"

        observation += f"Time: {time_of_day}\n\n"

        if voxels:
            observation += f"Nearby blocks: {', '.join(voxels)}\n\n"
        else:
            observation += f"Nearby blocks: None\n\n"

        observation += f"Health: {health:.1f}/20\n\n"
        observation += f"Hunger: {hunger:.1f}/20\n\n"

        observation += f"Position: x={position['x']:.1f}, y={position['y']:.1f}, z={position['z']:.1f}\n\n"

        observation += f"Equipment: {equipment}\n\n"

        if inventory:
            observation += f"Inventory ({inventory_used}/36): {inventory}\n\n"
        else:
            observation += f"Inventory ({inventory_used}/36): Empty\n\n"

        observation += chest_observation

        if executed_skills:
            observation += (
                "Skills executed this round (name: result, inventory change):\n"
            )
            for rec in executed_skills:
                if not isinstance(rec, dict):
                    continue
                name = rec.get("name") or "?"
                if rec.get("ok"):
                    result = "ok"
                else:
                    err = rec.get("err") or "error"
                    result = f'error "{err}"'
                delta = rec.get("delta") or {}
                parts = []
                if isinstance(delta, dict):
                    for item, n in delta.items():
                        try:
                            n = int(n)
                        except (TypeError, ValueError):
                            pass
                        if isinstance(n, int) and n > 0:
                            parts.append(f'"{item}": +{n}')
                        else:
                            parts.append(f'"{item}": {n}')
                delta_s = "{" + ", ".join(parts) + "}"
                observation += f"- {name}: {result}, {delta_s}\n"
            observation += "\n"

        observation += f"Task: {task}\n\n"

        if context:
            observation += f"Context: {context}\n\n"
        else:
            observation += f"Context: None\n\n"

        print(f"\033[31m****Critic Agent human message****\n{observation}\033[0m")
        return HumanMessage(content=observation)

    def human_check_task_success(self):
        confirmed = False
        success = False
        critique = ""
        while not confirmed:
            success = input("Success? (y/n)")
            success = success.lower() == "y"
            critique = input("Enter your critique:")
            print(f"Success: {success}\nCritique: {critique}")
            confirmed = input("Confirm? (y/n)") in ["y", ""]
        return success, critique, {}

    def ai_check_task_success(self, messages, max_retries=5):
        if max_retries == 0:
            print(
                "\033[31mFailed to parse Critic Agent response. Consider updating your prompt.\033[0m"
            )
            return False, "", {}

        if messages[1] is None:
            return False, "", {}

        critic = call_llm_with_retry(
            self.llm, messages, label="critic.check_task_success"
        ).content
        print(f"\033[31m****Critic Agent ai message****\n{critic}\033[0m")
        try:
            response = fix_and_parse_json(critic)
            assert response["success"] in [True, False]
            if "critique" not in response:
                response["critique"] = ""
            skill_credit = response.get("skill_credit")
            if not isinstance(skill_credit, dict):
                skill_credit = {}
            else:
                skill_credit = {
                    str(k): bool(v) for k, v in skill_credit.items()
                }
            return response["success"], response["critique"], skill_credit
        except Exception as e:
            print(f"\033[31mError parsing critic response: {e} Trying again!\033[0m")
            return self.ai_check_task_success(
                messages=messages,
                max_retries=max_retries - 1,
            )

    def check_task_success(
        self,
        *,
        events,
        task,
        context,
        chest_observation,
        max_retries=5,
        executed_skills=None,
    ):
        human_message = self.render_human_message(
            events=events,
            task=task,
            context=context,
            chest_observation=chest_observation,
            executed_skills=executed_skills,
        )

        messages = [
            self.render_system_message(),
            human_message,
        ]

        if self.mode == "manual":
            return self.human_check_task_success()
        elif self.mode == "auto":
            return self.ai_check_task_success(
                messages=messages, max_retries=max_retries
            )
        else:
            raise ValueError(f"Invalid critic agent mode: {self.mode}")
