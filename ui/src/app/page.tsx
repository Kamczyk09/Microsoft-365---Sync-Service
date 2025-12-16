// src/app/page.tsx
"use client";   // MUST be first line

import { useState, useEffect } from "react";

interface User {
  email: string;
  display_name: string;
  expires_at: number;
}

export default function HomePage() {
  const [users, setUsers] = useState<User[]>([]);

  useEffect(() => {
    async function fetchUsers() {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/users`);
      const data = await res.json();
      setUsers(data);
    }

    fetchUsers();
    const interval = setInterval(fetchUsers, 10000);
    return () => clearInterval(interval);
  }, []);

  async function handleSignIn() {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/auth/mslogin/url`);
    const data = await res.json();
    window.location.href = data.url;  // redirect to Microsoft login
  }

  return (
    <main style={{ padding: "2rem" }}>
      <h1>OneDrive Sync Dashboard</h1>
      <button onClick={handleSignIn}>Sign in with Microsoft</button>
      {users.map(user => (
        <div key={user.email} style={{ border: "1px solid #ccc", padding: "1rem", margin: "1rem 0" }}>
          <h2>{user.display_name}</h2>
          <p>Email: {user.email}</p>
          <p>Token expires at: {new Date(user.expires_at * 1000).toLocaleString()}</p>
        </div>
      ))}
    </main>
  );
}
