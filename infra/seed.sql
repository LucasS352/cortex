INSERT INTO tenants (name, slug, erp_system_name)
VALUES ('Alternativa Sistemas LTDA', 'alternativa', 'Master')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO connectors (tenant_id, provider, api_base_url)
SELECT id, 'movidesk', 'https://api.movidesk.com/public/v1'
FROM tenants WHERE slug = 'alternativa'
ON CONFLICT DO NOTHING;
