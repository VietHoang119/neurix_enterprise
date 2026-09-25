import { useState } from "react";
import { FirebaseError } from "firebase/app";
import { signInWithGoogle } from "../lib/firebase";
import Logo from "../components/Logo";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";

// Firebase errors read "Firebase: Error (auth/…)." — an internal code, never shown as is.
function signInError(err: unknown): string | null {
  if (err instanceof FirebaseError) {
    if (err.code === "auth/popup-closed-by-user" || err.code === "auth/cancelled-popup-request") return null;
    if (err.code === "auth/popup-blocked")
      return "Your browser blocked the sign-in window. Allow pop-ups for this site and try again.";
    if (err.code === "auth/network-request-failed")
      return "Couldn't reach Google. Check your connection and try again.";
  }
  return "Sign-in didn't work. Please try again.";
}

export default function Login() {
  const [error, setError] = useState<string | null>(null);
  const [isSigningIn, setIsSigningIn] = useState(false);

  const handleSignIn = async () => {
    setError(null);
    setIsSigningIn(true);
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(signInError(err));
    } finally {
      setIsSigningIn(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm p-8 text-center">
        <div className="mb-4 flex items-center justify-center gap-2">
          <Logo className="h-8 w-8" />
          <span className="text-2xl font-semibold tracking-tight">Neurix</span>
          <span className="text-sm text-subtle-foreground">Enterprise</span>
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          Sign in with your organization's Google account to continue.
        </p>
        <Button onClick={handleSignIn} disabled={isSigningIn} className="w-full">
          {isSigningIn ? "Signing in…" : "Sign in with Google"}
        </Button>
        {error && <p className="mt-4 text-sm text-status-failed">{error}</p>}
      </Card>
    </div>
  );
}
