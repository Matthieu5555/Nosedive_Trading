import { useState } from "react";

import { AsyncBlock } from "../components/AsyncBlock";
import { useFetch } from "../hooks/useFetch";

interface ConfigList {
  files: string[];
}
interface ConfigFile {
  filename: string;
  content: string;
}

export function ConfigPage() {
  const list = useFetch<ConfigList>("/api/config");
  const [selected, setSelected] = useState<string | null>(null);
  const file = useFetch<ConfigFile>(
    selected ? `/api/config/${encodeURIComponent(selected)}` : "/api/config",
  );

  return (
    <section>
      <h1>Configuration</h1>
      <AsyncBlock state={list}>
        {(data) => (
          <ul>
            {data.files.map((name) => (
              <li key={name}>
                <button type="button" onClick={() => setSelected(name)}>
                  {name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </AsyncBlock>
      {selected !== null && file.data !== null && "content" in file.data && (
        <pre aria-label="config-content">{(file.data as ConfigFile).content}</pre>
      )}
    </section>
  );
}
