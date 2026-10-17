import { useState } from "react";
import { Star, GitFork, MessageCircleCode, Calendar } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Repository } from "@/types";
import { getLanguageColor } from "@/lib/colors";
import { ChatAssistant } from "@/components/dashboard/repository/ChatAssistant";

interface RepositoryCardProps {
  repo: Repository;
}

export const RepositoryCard = ({ repo }: RepositoryCardProps) => {
  const [isChatOpen, setIsChatOpen] = useState(false);

  const toggleChat = () => {
    setIsChatOpen(!isChatOpen);
  };

  // Format date for better readability
  const formatDate = (dateString: string) => {
    try {
      const date = new Date(dateString);
      const now = new Date();
      const diffTime = Math.abs(now.getTime() - date.getTime());
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
      
      if (diffDays === 1) return "Today";
      if (diffDays < 7) return `${diffDays} days ago`;
      if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
      if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
      return `${Math.floor(diffDays / 365)} years ago`;
    } catch {
      return "Unknown";
    }
  };

  // Safely get language color
  const getSafeLanguageColor = (language: string | null) => {
    if (!language) return "#6B7280";
    return getLanguageColor(language) || "#6B7280";
  };

  return (
    <>
      <div className="flex items-center justify-between py-4 hover:bg-gray-50 dark:hover:bg-zinc-900 transition-colors duration-200">
        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium truncate">{repo.name}</h2>
            <Badge className="bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300" variant="outline">
              {repo.private ? "Private" : "Public"}
            </Badge>
          </div>
          
          {repo.description && (
            <p className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">
              {repo.description}
            </p>
          )}
          
          <div className="mt-1 flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400 flex-wrap">
            {repo.language && (
              <div className="flex items-center gap-1">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: getSafeLanguageColor(repo.language) }}
                />
                {repo.language}
              </div>
            )}
            
            {repo.stars !== undefined && (
              <span className="flex items-center gap-1">
                <Star className="h-4 w-4" />
                {repo.stars.toLocaleString()}
              </span>
            )}
            
            {repo.forks !== undefined && (
              <span className="flex items-center gap-1">
                <GitFork className="h-4 w-4" />
                {repo.forks.toLocaleString()}
              </span>
            )}
            
            {repo.lastUpdated && (
              <span className="flex items-center gap-1">
                <Calendar className="h-4 w-4" />
                {formatDate(repo.lastUpdated)}
              </span>
            )}
          </div>
        </div>
        
        <div className="flex items-center gap-2 ml-4">
          <button
            onClick={toggleChat}
            className="p-2 hover:bg-gray-100 dark:hover:bg-zinc-800 rounded-full transition-colors duration-200"
            aria-label="Open code assistant"
            title="Open code assistant"
          >
            <MessageCircleCode className="h-5 w-5 text-blue-600 dark:text-blue-400" />
          </button>
        </div>
      </div>

      {/* Render chat assistant when open */}
      {isChatOpen && (
        <ChatAssistant 
          owner={typeof repo.owner === 'object' && repo.owner ? (repo.owner as any).login : repo.owner} 
          repo={repo.name} 
          onClose={() => setIsChatOpen(false)} 
        />
      )}
    </>
  );
};
