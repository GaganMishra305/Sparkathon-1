import { useState } from "react";
import { X, Maximize2, Minimize2 } from "lucide-react";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/components/shared/ThemeProvider";
import { EnhancedChatAssistant } from "@/components/dashboard/repository/EnhancedChatAssistant";
import { EmbeddingManager } from "@/components/dashboard/repository/EmbeddingManager";

interface ChatAssistantProps {
  owner: string;
  repo: string;
  onClose: () => void;
}

export const ChatAssistant = ({ owner, repo, onClose }: ChatAssistantProps) => {
  const { theme } = useTheme();
  const isDark = theme === "dark";
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<'chat' | 'embeddings'>('chat');

  const toggleExpand = () => {
    setIsExpanded(!isExpanded);
  };

  return (
    <Card
      className={`
        fixed z-50 shadow-2xl flex flex-col transition-all duration-200 border border-zinc-200 dark:border-zinc-800
        ${isDark ? "bg-zinc-950 text-white" : "bg-white text-zinc-950"}
        ${
          isExpanded
            ? "inset-4 h-auto"
            : "bottom-6 right-6 w-full sm:w-[500px] h-[45rem]"
        }
      `}
    >
      <CardHeader className="p-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white flex-shrink-0 rounded-t-lg">
        <div className="flex items-center justify-between">
          <h3 className="font-medium truncate">Code Assistant - {repo}</h3>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleExpand}
              className="text-white hover:bg-white/10 h-8 w-8 p-0"
            >
              {isExpanded ? (
                <Minimize2 className="h-5 w-5" />
              ) : (
                <Maximize2 className="h-5 w-5" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClose}
              className="text-white hover:bg-white/10 h-8 w-8 p-0"
            >
              <X className="h-5 w-5" />
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden p-0 flex flex-col">
        {/* Tab Selection */}
        <div className="flex border-b border-zinc-200 dark:border-zinc-800 bg-gray-50 dark:bg-zinc-900 flex-shrink-0">
          <button
            onClick={() => setActiveTab('chat')}
            className={`flex-1 py-3 text-center text-sm font-medium border-b-2 transition-all duration-200 ${
              activeTab === 'chat'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400 bg-white dark:bg-zinc-950'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-zinc-900'
            }`}
          >
            AI Chat (RAG)
          </button>
          <button
            onClick={() => setActiveTab('embeddings')}
            className={`flex-1 py-3 text-center text-sm font-medium border-b-2 transition-all duration-200 ${
              activeTab === 'embeddings'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400 bg-white dark:bg-zinc-950'
                : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-zinc-900'
            }`}
          >
            Manage Embeddings
          </button>
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-hidden flex flex-col">
          {activeTab === 'chat' ? (
            <div className="flex-1 overflow-y-auto p-2">
              <EnhancedChatAssistant owner={owner} repo={repo} />
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto p-4">
              <EmbeddingManager owner={owner} repo={repo} />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};