import { redirect } from "next/navigation";

// Route moved to /homepage — this is a backward-compat redirect only
export const dynamic = "force-dynamic";

export default function LovableRedirect() {
  redirect("/homepage");
}
