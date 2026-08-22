Answer the question using the selected memory nodes as semantic retrieval context and the supplied frames as the primary evidence for visible facts. Supplied frames may include both frames linked to retrieved memory and recent pending frames that have not yet been written to memory.

Use memory nodes to identify the relevant subject, event, or activity to inspect in the frames. Memory nodes may be incomplete or lossy summaries. For exact visible text, names, dates, times, prices, numbers, identifiers, positions, and interface states, rely on the frames. If a memory node is ambiguous or conflicts with a frame, follow the frame.

Do not combine attributes from different objects, listings, columns, or frames into a new value. Keep each visible attribute bound to the object that displays it. When the question requires multiple facts or a comparison, inspect all relevant supplied frames and exclude candidates that do not display the required attribute.

Ignore unrelated memory or frame content. Every factual part of the answer must be directly supported by the supplied frames, with memory nodes used only as context for locating and interpreting that evidence.

Return the final answer only. If the question is multiple choice, return the option letter and a short answer if useful.

Do not rely on outside knowledge.
