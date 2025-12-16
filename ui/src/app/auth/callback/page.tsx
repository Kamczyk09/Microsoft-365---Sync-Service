"use client"

import { useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"

export default function CallbackPage() {
  const params = useSearchParams()
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // 🔴 1. HANDLE MICROSOFT OAUTH ERRORS FIRST
    const oauthError = params.get("error")
    const errorDescription = params.get("error_description")

    if (oauthError) {
      setError(errorDescription || oauthError)
      return // ⛔ DO NOT call backend
    }

    // ✅ 2. SUCCESS PATH
    const code = params.get("code")
    if (!code) {
      setError("Missing authorization code")
      return
    }

    fetch(
  `${process.env.NEXT_PUBLIC_API_BASE_URL}/auth/exchange?code=${encodeURIComponent(code)}`
    )

      .then(async res => {
        if (!res.ok) {
          const data = await res.json()
          throw new Error(data.error || "Authorization failed")
        }
        router.push("/")
      })
      .catch(err => setError(err.message))
  }, [params, router])

  // 🔵 3. RENDER USER-FACING FEEDBACK
  if (error) {
    return (
      <div>
        <h2>Authorization failed</h2>
        <p>{error}</p>
        <a href="/">Go back</a>
      </div>
    )
  }

  return <p>Authorizing Microsoft account…</p>
}
