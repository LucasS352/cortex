-- 010_technician_pins.sql
-- Tabela para gerenciamento de PINs de técnicos e curadores de treinamento

CREATE TABLE IF NOT EXISTS cortex_technician_pins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    pin VARCHAR(8) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by VARCHAR(100) DEFAULT 'lucas.souza',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cortex_technician_pins_pin ON cortex_technician_pins (pin);

INSERT INTO cortex_technician_pins (name, pin, is_active, created_by) VALUES
    ('Renan', '1001', TRUE, 'lucas.souza'),
    ('Marília', '0000', TRUE, 'lucas.souza'),
    ('Lucas Souza', '1234', TRUE, 'lucas.souza'),
    ('Luiz Araújo', '3521', TRUE, 'lucas.souza'),
    ('Izabelle', '3522', TRUE, 'lucas.souza'),
    ('Murilo', '3523', TRUE, 'lucas.souza'),
    ('Ingrid', '3524', TRUE, 'lucas.souza'),
    ('Vinícius Garcia', '3525', TRUE, 'lucas.souza')
ON CONFLICT (pin) DO NOTHING;
