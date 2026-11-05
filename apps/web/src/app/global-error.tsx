"use client";
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main>
          <h1>AgentProof could not load.</h1>
          <p>Try loading the application again.</p>
          <button onClick={reset}>Try again</button>
        </main>
      </body>
    </html>
  );
}
