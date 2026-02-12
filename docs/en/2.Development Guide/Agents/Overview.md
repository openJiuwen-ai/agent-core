openJiuwen is dedicated to providing efficient and flexible support for Agent application development, helping users quickly build intelligent and automated Agent application systems to easily handle various complex tasks. Currently, openJiuwen provides two built-in agents, `ReActAgent` and `WorkflowAgent`, offering rich features and flexible development options to meet intelligent requirements in different user scenarios.

# ReActAgent

`ReActAgent` is an Agent that follows the ReAct pattern, completing user tasks through a cyclical iteration of "Thought → Action → Observe". First, the model performs logical reasoning and planning, selecting and calling appropriate tools (such as retrieval, database, third-party API, code execution, etc.) to perform specific operations; second, based on the execution results (Observation) and feedback, it constantly adjusts strategies and optimizes reasoning paths until the task goal is achieved or a final answer is obtained. Its powerful multi-turn reasoning and self-correction capabilities enable `ReActAgent` to possess dynamic decision-making abilities and flexibly adapt to environmental changes, making it suitable for diverse task scenarios requiring complex reasoning and strategy adjustment.

## Supported Scenarios

-   **Non-streaming invocation**: Call the `invoke` interface of `ReActAgent`, input the user request; output the overall response to the user request after the agent calls several tools.
-   **Streaming invocation**: Call the `stream` interface of `ReActAgent`, input the user request; stream output data frames of `ReActAgent`'s overall response to the user request.

## Application Scenarios

`ReActAgent` is suitable for scenarios requiring real-time response, dynamic decision-making, and interaction capabilities. It possesses high flexibility and adaptability, making it suitable for handling unstructured or frequently changing tasks.

-   **Intelligent Customer Service**: Real-time understanding of user intent to provide personalized responses. It not only answers common questions but also reasons and guides based on user emotions and context, enhancing the service experience.
-   **Smart Home**: Real-time adjustment of the home environment based on sensor data, such as automatically adjusting room temperature, light brightness, playing background music, etc., improving living comfort and energy efficiency.

# WorkflowAgent

`WorkflowAgent` is a process automation Agent focused on multi-step, task-oriented execution. It efficiently executes complex tasks by strictly following a user-predefined task process. Users can pre-set clear task steps, execution conditions, and role assignments, decomposing the task into multiple executable sub-tasks or tools. Through topological connections and data transmission between components, it progressively advances the entire workflow, finally outputting the expected result. It focuses on achieving standardized and efficient execution of tasks based on preset processes, suitable for scenarios where the task structure is clear and can be decomposed into multiple steps.

## Supported Scenarios

-   **Non-streaming invocation**: Call the `invoke` interface of `WorkflowAgent`, input the input parameters relied upon by the workflow; output the execution result of the workflow.
-   **Streaming invocation**: Call the `stream` interface of `WorkflowAgent`, similarly input the input parameters relied upon by the workflow; stream output several data frames, including streaming output data frames of the workflow associated with `WorkflowAgent`, debugging information data frames, and the data frame of the final execution result of `WorkflowAgent`.
-   **Human-in-the-loop (HITL)**: When the workflow associated with `WorkflowAgent` contains components with interrupt recovery capabilities (for example, the Questioner component in built-in components), it may trigger a Human-in-the-loop process. Based on whether the return result of `WorkflowAgent` contains a follow-up question for the user, it can be determined whether the HITL process has been triggered. Once the HITL process is triggered, developers need to use the user's feedback information as the input for `WorkflowAgent` and call the execution flow of `WorkflowAgent` again until `WorkflowAgent` returns the final execution result.

## Application Scenarios

`WorkflowAgent` focuses on process automation and task collaboration, suitable for task scenarios with clear structures, distinct steps, and high repetition, emphasizing efficient task execution and seamless integration between systems.

-   **Project Management**: Conducting task assignment, progress tracking, resource scheduling, etc., ensuring projects proceed smoothly according to established goals and schedules.
-   **Enterprise Automation**: Implementing automation of daily operations such as approval processes, data flow, and report generation, reducing manual intervention, lowering error rates, and improving operational efficiency.
-   **Data Processing and Analysis**: Integrating multi-source data, automatically cleaning, analyzing, and visualizing it, generating structured reports and analysis results, and transforming them into decision support.