import { AuthForm } from "@/components/auth-form";

export default function LoginPage() {
  return (
    <section className="auth-page">
      <AuthForm mode="login" />
    </section>
  );
}
