"use client";
export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <section className="center-page">
      <p className="eyebrow">SOMETHING WENT WRONG</p>
      <h1>This view could not load.</h1>
      <p>
        Try again. If the problem continues, check the local application logs.
      </p>
      <button className="button primary" onClick={reset}>
        Try again
      </button>
    </section>
  );
}
