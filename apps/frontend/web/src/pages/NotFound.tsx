import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section>
      <h1>Not found</h1>
      <p>
        That page does not exist. <Link to="/">Back home</Link>.
      </p>
    </section>
  );
}
