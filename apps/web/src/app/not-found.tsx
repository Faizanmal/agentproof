import Link from "next/link";
export default function NotFound() {
  return (
    <section className="center-page">
      <p className="eyebrow">PAGE NOT FOUND</p>
      <h1>No journey here.</h1>
      <p>This address does not point to an available page.</p>
      <Link className="button primary" href="/">
        Return home
      </Link>
    </section>
  );
}
