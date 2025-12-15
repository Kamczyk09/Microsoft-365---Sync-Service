import { User } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

export async function fetchUsers(): Promise<User[]> {
  const res = await fetch(`${API_BASE}/users`, {
    cache: "no-store"
  });

  if (!res.ok) {
    throw new Error("Failed to fetch users");
  }

  return res.json();
}
