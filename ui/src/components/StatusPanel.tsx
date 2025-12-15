"use client";

import { useEffect, useState } from "react";
import { fetchUsers } from "@/services/api";
import { User } from "@/types";

export default function StatusPanel() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUsers()
      .then(setUsers)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <p>Loading sync status...</p>;
  }

  if (users.length === 0) {
    return <p>No authenticated users found.</p>;
  }

  return (
    <div>
      {users.map(user => (
        <div
          key={user.id}
          style={{
            padding: "1rem",
            background: "white",
            marginBottom: "1rem",
            borderRadius: "6px"
          }}
        >
          <strong>{user.display_name}</strong>
          <div>Email: {user.email}</div>
          <div>Token expires at: {new Date(user.expires_at * 1000).toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}
