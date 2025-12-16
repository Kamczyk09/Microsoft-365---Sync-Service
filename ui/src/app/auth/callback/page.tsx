// src/app/auth/callback/page.tsx
"use client";  // MUST be first line

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function CallbackPage() {
  const router = useRouter();

  useEffect(() => {
    async function finishLogin() {
      const urlParams = new URLSearchParams(window.location.search);
      const code = urlParams.get("code");
      if (!code) return;

      await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/auth/callback?code=${code}`);
      router.push("/");  // redirect back to dashboard
    }
    finishLogin();
  }, [router]);

  return <p>Completing login, please wait...</p>;
}
