import { cloneElement, isValidElement, type ElementType, type ReactElement, type ReactNode } from "react";
import { Inbox } from "lucide-react";

type IconComponent = ElementType<{ size?: string | number; className?: string }>;

type EmptyStateProps = {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: IconComponent | ReactNode;
};

function renderIcon(icon: EmptyStateProps["icon"]) {
  if (isValidElement(icon)) {
    return cloneElement(icon as ReactElement<{ className?: string }>, {
      className: ["text-primary", icon.props.className].filter(Boolean).join(" "),
    });
  }

  const Icon = typeof icon === "function" ? icon : Inbox;
  return <Icon size={28} className="text-primary" />;
}

export function EmptyState({ title, description, action, icon = Inbox }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="w-14 h-14 rounded-full bg-canvas flex items-center justify-center mb-4">
        {renderIcon(icon)}
      </div>
      <h3 className="text-base font-semibold text-ink">{title}</h3>
      {description && <p className="text-sm text-gray-500 mt-1 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
