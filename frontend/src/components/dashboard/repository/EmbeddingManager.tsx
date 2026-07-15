import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, Database, RefreshCw, Trash2, CheckCircle, AlertCircle } from 'lucide-react';
import useAuth from '@/hooks/useAuth';

interface EmbeddingStatus {
  repoId: string;
  exists: boolean;
  fileCount: number;
  totalChunks: number;
  files?: string[];
}

interface EmbeddingManagerProps {
  owner: string;
  repo: string;
  onEmbeddingsCreated?: () => void;
}

export const EmbeddingManager: React.FC<EmbeddingManagerProps> = ({
  owner,
  repo,
  onEmbeddingsCreated
}) => {
  const { user } = useAuth();
  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [recreating, setRecreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const repoId = `${owner}/${repo}`;

  const checkEmbeddingsStatus = async () => {
    if (!user) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`/api/chatbot/embeddings/${encodeURIComponent(repoId)}`, {
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      
      if (!response.ok) {
        throw new Error('Failed to check embeddings status');
      }
      
      const data = await response.json();
      setStatus(data.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check status');
    } finally {
      setLoading(false);
    }
  };

  const createEmbeddings = async () => {
    if (!user) return;
    
    setCreating(true);
    setError(null);
    setSuccess(null);
    
    try {
      const response = await fetch('/api/chatbot/embeddings', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          owner,
          repo
        }),
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || 'Failed to create embeddings');
      }
      
      const data = await response.json();
      setSuccess(`Embeddings created successfully! ${data.data.fileCount} files with ${data.data.totalChunks} chunks.`);
      await checkEmbeddingsStatus();
      onEmbeddingsCreated?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create embeddings');
    } finally {
      setCreating(false);
    }
  };

  const recreateEmbeddings = async () => {
    if (!user) return;
    
    setRecreating(true);
    setError(null);
    setSuccess(null);
    
    try {
      const response = await fetch('/api/chatbot/embeddings/recreate', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          owner,
          repo
        }),
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || 'Failed to recreate embeddings');
      }
      
      const data = await response.json();
      setSuccess(`Embeddings recreated successfully! ${data.data.fileCount} files with ${data.data.totalChunks} chunks.`);
      await checkEmbeddingsStatus();
      onEmbeddingsCreated?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to recreate embeddings');
    } finally {
      setRecreating(false);
    }
  };

  const deleteEmbeddings = async () => {
    if (!user) return;
    
    if (!confirm('Are you sure you want to delete the embeddings? This action cannot be undone.')) {
      return;
    }
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`/api/chatbot/embeddings/${encodeURIComponent(repoId)}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      
      if (!response.ok) {
        throw new Error('Failed to delete embeddings');
      }
      
      setStatus(null);
      setSuccess('Embeddings deleted successfully');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete embeddings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkEmbeddingsStatus();
  }, [owner, repo]);

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-5 w-5" />
          Embeddings Manager
        </CardTitle>
        <CardDescription>
          Manage AI embeddings for {owner}/{repo}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Status Display */}
        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking embeddings status...
          </div>
        ) : status ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              <span className="font-medium">Embeddings Available</span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Badge variant="secondary">{status.fileCount} Files</Badge>
              </div>
              <div>
                <Badge variant="secondary">{status.totalChunks} Chunks</Badge>
              </div>
            </div>
            {status.files && status.files.length > 0 && (
              <div>
                <p className="text-sm text-muted-foreground mb-2">Sample files:</p>
                <div className="space-y-1">
                  {status.files.map((file, index) => (
                    <div key={index} className="text-xs text-muted-foreground font-mono">
                      {file}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-muted-foreground">
            <AlertCircle className="h-5 w-5" />
            <span>No embeddings found</span>
          </div>
        )}

        {/* Error Display */}
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Success Display */}
        {success && (
          <Alert>
            <CheckCircle className="h-4 w-4" />
            <AlertDescription>{success}</AlertDescription>
          </Alert>
        )}

        {/* Action Buttons */}
        <div className="flex flex-wrap gap-2">
          {!status?.exists ? (
            <Button
              onClick={createEmbeddings}
              disabled={creating}
              className="flex items-center gap-2"
            >
              {creating ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Database className="h-4 w-4" />
              )}
              {creating ? 'Creating...' : 'Create Embeddings'}
            </Button>
          ) : (
            <>
              <Button
                onClick={recreateEmbeddings}
                disabled={recreating}
                variant="outline"
                className="flex items-center gap-2"
              >
                {recreating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                {recreating ? 'Recreating...' : 'Recreate Embeddings'}
              </Button>
              <Button
                onClick={deleteEmbeddings}
                disabled={loading}
                variant="destructive"
                className="flex items-center gap-2"
              >
                <Trash2 className="h-4 w-4" />
                Delete Embeddings
              </Button>
            </>
          )}
          <Button
            onClick={checkEmbeddingsStatus}
            disabled={loading}
            variant="ghost"
            size="sm"
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>

        {/* Info */}
        <div className="text-xs text-muted-foreground space-y-1">
          <p>• Embeddings enable AI-powered code analysis and chat</p>
          <p>• Large repositories are automatically chunked for better performance</p>
          <p>• Embeddings are stored locally and persist between sessions</p>
        </div>
      </CardContent>
    </Card>
  );
}; 