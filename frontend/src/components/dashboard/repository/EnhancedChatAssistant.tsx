import React, { useState, useRef, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, Send, Bot, User, AlertCircle, CheckCircle, RefreshCw } from 'lucide-react';
import useAuth from '@/hooks/useAuth';

interface Message {
  id: string;
  type: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  provider?: string;
  relevantFiles?: Array<{ path: string; score: number }>;
  confidence?: number;
}

interface ChatAssistantProps {
  owner: string;
  repo: string;
  repoId?: string;
}

export const EnhancedChatAssistant: React.FC<ChatAssistantProps> = ({
  owner,
  repo,
  repoId
}) => {
  const { user } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [provider, setProvider] = useState<'gemini' | 'groq'>('groq');
  const [error, setError] = useState<string | null>(null);
  const [embeddingsStatus, setEmbeddingsStatus] = useState<{
    exists: boolean;
    fileCount: number;
    totalChunks: number;
  } | null>(null);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const finalRepoId = repoId || `${owner}/${repo}`;

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    checkEmbeddingsStatus();
  }, [owner, repo]);

  const checkEmbeddingsStatus = async () => {
    if (!user) return;
    
    try {
      const response = await fetch(`/api/chatbot/embeddings/${encodeURIComponent(finalRepoId)}`, {
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      
      if (response.ok) {
        const data = await response.json();
        setEmbeddingsStatus(data.data);
      }
    } catch (error) {
      console.error('Error checking embeddings status:', error);
    }
  };

  const sendMessage = async () => {
    if (!input.trim() || !user) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/chatbot/chat', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          repoId: finalRepoId,
          query: input.trim(),
          provider,
          owner,
          repo
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || 'Failed to get response');
      }

      const data = await response.json();
      
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: data.data.answer,
        timestamp: new Date(),
        provider: data.data.provider,
        relevantFiles: data.data.relevantFiles,
        confidence: data.data.confidence,
      };

      setMessages(prev => [...prev, assistantMessage]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    setMessages([]);
    setError(null);
  };

  const formatConfidence = (confidence: number) => {
    const percentage = Math.round(confidence * 100);
    if (percentage >= 80) return { text: 'High', color: 'bg-green-500' };
    if (percentage >= 60) return { text: 'Medium', color: 'bg-yellow-500' };
    return { text: 'Low', color: 'bg-red-500' };
  };

  return (
    <Card className="w-full h-[600px] flex flex-col">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="h-5 w-5" />
          AI Chat Assistant
        </CardTitle>
        <CardDescription>
          Ask questions about {owner}/{repo} codebase
        </CardDescription>
        
        {/* Provider Selection */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">AI Provider:</span>
            <Select value={provider} onValueChange={(value: 'gemini' | 'groq') => setProvider(value)}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="gemini">Gemini</SelectItem>
                <SelectItem value="groq">Groq</SelectItem>
              </SelectContent>
            </Select>
          </div>
          
          {embeddingsStatus && (
            <div className="flex items-center gap-2">
              {embeddingsStatus.exists ? (
                <Badge variant="secondary" className="flex items-center gap-1">
                  <CheckCircle className="h-3 w-3" />
                  {embeddingsStatus.fileCount} files, {embeddingsStatus.totalChunks} chunks
                </Badge>
              ) : (
                <Badge variant="destructive" className="flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  No embeddings
                </Badge>
              )}
            </div>
          )}
        </div>
      </CardHeader>

      <CardContent className="flex-1 flex flex-col space-y-4">
        {/* Messages */}
        <ScrollArea className="flex-1 border rounded-md p-4">
          {messages.length === 0 ? (
            <div className="text-center text-muted-foreground py-8">
              <Bot className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>Start a conversation about the codebase</p>
              <p className="text-sm">Try asking about security vulnerabilities, code quality, or specific features</p>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`flex gap-3 ${
                    message.type === 'user' ? 'justify-end' : 'justify-start'
                  }`}
                >
                  <div
                    className={`flex gap-3 max-w-[80%] ${
                      message.type === 'user' ? 'flex-row-reverse' : 'flex-row'
                    }`}
                  >
                    <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                      message.type === 'user' 
                        ? 'bg-blue-500 text-white' 
                        : 'bg-gray-200 text-gray-700'
                    }`}>
                      {message.type === 'user' ? (
                        <User className="h-4 w-4" />
                      ) : (
                        <Bot className="h-4 w-4" />
                      )}
                    </div>
                    
                    <div className={`rounded-lg p-3 ${
                      message.type === 'user' 
                        ? 'bg-blue-500 text-white' 
                        : 'bg-gray-100 text-gray-900'
                    }`}>
                      <div className="whitespace-pre-wrap">{message.content}</div>
                      
                      {message.type === 'assistant' && (
                        <div className="mt-2 space-y-2">
                          {message.provider && (
                            <div className="flex items-center gap-2 text-xs opacity-70">
                              <span>Provider: {message.provider}</span>
                              {message.confidence && (
                                <Badge 
                                  variant="outline" 
                                  className={`text-xs ${formatConfidence(message.confidence).color} text-white`}
                                >
                                  {formatConfidence(message.confidence).text} Confidence
                                </Badge>
                              )}
                            </div>
                          )}
                          
                          {message.relevantFiles && message.relevantFiles.length > 0 && (
                            <div className="text-xs opacity-70">
                              <div className="font-medium mb-1">Relevant files:</div>
                              <div className="space-y-1">
                                {message.relevantFiles.slice(0, 3).map((file, index) => (
                                  <div key={index} className="font-mono text-xs">
                                    {file.path} ({(file.score * 100).toFixed(1)}%)
                                  </div>
                                ))}
                                {message.relevantFiles.length > 3 && (
                                  <div className="text-xs opacity-50">
                                    +{message.relevantFiles.length - 3} more files
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              
              {loading && (
                <div className="flex gap-3 justify-start">
                  <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center">
                    <Bot className="h-4 w-4" />
                  </div>
                  <div className="bg-gray-100 rounded-lg p-3">
                    <div className="flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span className="text-sm">Thinking...</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
          <div ref={messagesEndRef} />
        </ScrollArea>

        {/* Error Display */}
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Input Area */}
        <div className="flex gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Ask about the codebase..."
            className="flex-1 resize-none"
            rows={2}
            disabled={loading}
          />
          <div className="flex flex-col gap-2">
            <Button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              size="sm"
              className="flex items-center gap-2"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
            <Button
              onClick={clearChat}
              variant="outline"
              size="sm"
              className="flex items-center gap-2"
            >
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}; 